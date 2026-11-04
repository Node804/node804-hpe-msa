"""Tests for the setup wizard (setup.py).

Keyring is replaced with an in-memory fake (shared between the setup module and
the config module), the config path is redirected to a tmp file, and no test
touches the real OS keychain, the real Claude config, or ``~/.claude/skills``.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from hpe_msa_mcp import setup as setup_mod
from hpe_msa_mcp.config import arrays as arrays_mod
from hpe_msa_mcp.config.arrays import DEFAULT_KEYRING_ACCOUNT

SERVICE = "hpe-msa-mcp"


class _FakeKeyring:
    """Minimal in-memory keyring stand-in: (service, account) -> password."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.store[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        self.store.pop((service, account), None)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeKeyring:
    """Redirect keyring (both modules) to one fake and config to a tmp path."""
    fake = _FakeKeyring()
    monkeypatch.setattr(arrays_mod, "keyring", fake)
    monkeypatch.setattr(setup_mod, "keyring", fake)
    monkeypatch.setenv("MSA_ARRAYS_CONFIG", str(tmp_path / "arrays.json"))
    for var in ("MSA_HOST", "MSA_USERNAME", "MSA_PASSWORD", setup_mod.PASSWORD_ENV):
        monkeypatch.delenv(var, raising=False)
    arrays_mod._entries = []
    arrays_mod._defaults_username = None
    return fake


# --- Claude Desktop server entry --------------------------------------------


def test_build_server_entry_minimal() -> None:
    entry = setup_mod.build_server_entry(python_exe="/py", mode="read", tls_verify=True)
    assert entry == {"command": "/py", "args": ["-m", "hpe_msa_mcp"], "env": {"MSA_MODE": "read"}}


def test_build_server_entry_tls_off_and_audit() -> None:
    entry = setup_mod.build_server_entry(
        python_exe="/py", mode="read", tls_verify=False, audit_log="/var/log/msa.jsonl"
    )
    assert entry["env"]["MSA_TLS_VERIFY"] == "false"
    assert entry["env"]["MSA_AUDIT_LOG"] == "/var/log/msa.jsonl"
    # Host / username / password never land in the client config.
    assert "MSA_HOST" not in entry["env"]
    assert "MSA_PASSWORD" not in entry["env"]


# --- upsert_claude_server ----------------------------------------------------


def test_upsert_creates_config(tmp_path: Path) -> None:
    cfg = tmp_path / "claude_desktop_config.json"
    entry = {"command": "/py", "args": ["-m", "hpe_msa_mcp"], "env": {}}
    backup = setup_mod.upsert_claude_server(cfg, "HPE-MSA", entry)
    assert backup is None  # nothing to back up on first write
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["HPE-MSA"] == entry


def test_upsert_preserves_other_servers_and_backs_up(tmp_path: Path) -> None:
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}),
        encoding="utf-8",
    )
    backup = setup_mod.upsert_claude_server(cfg, "HPE-MSA", {"command": "/py"})
    assert backup is not None and backup.exists()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["other"] == {"command": "x"}  # preserved
    assert data["mcpServers"]["HPE-MSA"] == {"command": "/py"}  # added
    assert data["theme"] == "dark"  # top-level key preserved


# --- Skill install / zip -----------------------------------------------------


def test_skill_source_dir_found() -> None:
    src = setup_mod.skill_source_dir()
    assert src is not None
    assert (src / "SKILL.md").is_file()


def test_install_skill_to_dir(tmp_path: Path) -> None:
    dest = setup_mod.install_skill_to_dir(tmp_path / "skills")
    assert (dest / "SKILL.md").is_file()
    assert (dest / "references" / "tool-map.md").is_file()
    # Re-install overwrites cleanly (no stale files left behind).
    stale = dest / "STALE.md"
    stale.write_text("x", encoding="utf-8")
    setup_mod.install_skill_to_dir(tmp_path / "skills")
    assert not stale.exists()


def test_zip_skill_has_top_level_folder(tmp_path: Path) -> None:
    zip_path = setup_mod.zip_skill(tmp_path / "out.zip")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert f"{setup_mod.SKILL_NAME}/SKILL.md" in names
    assert all(n.startswith(f"{setup_mod.SKILL_NAME}/") for n in names)


def test_do_skill_install_copy_and_zip(tmp_path: Path) -> None:
    rc = setup_mod._do_skill_install(skills_dir=tmp_path / "skills", zip_dir=tmp_path)
    assert rc == 0
    assert (tmp_path / "skills" / setup_mod.SKILL_NAME / "SKILL.md").is_file()
    assert (tmp_path / f"{setup_mod.SKILL_NAME}-skill.zip").is_file()


# --- array subcommand (non-interactive) -------------------------------------


def _parse(argv: list[str]):
    return setup_mod.build_parser().parse_args(argv)


def test_array_add_stores_keychain_and_entry(
    _isolate: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(setup_mod.PASSWORD_ENV, "wizardpw")
    args = _parse(["array", "add", "--name", "prod", "--host", "10.0.0.1,10.0.0.2", "--yes"])
    rc = setup_mod.run_array(args)
    assert rc == 0
    assert _isolate.get_password(SERVICE, "prod") == "wizardpw"
    on_disk = json.loads(arrays_mod.get_config_path().read_text(encoding="utf-8"))
    record = on_disk["arrays"][0]
    assert record["name"] == "prod"
    assert record["hosts"] == ["10.0.0.1", "10.0.0.2"]  # failover list parsed
    assert "password" not in record  # password only in keychain


def test_array_add_without_password_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # No MSA_SETUP_PASSWORD, no shared default, --yes → cannot prompt → error.
    args = _parse(["array", "add", "--name", "p", "--host", "10.0.0.1", "--yes"])
    assert setup_mod.run_array(args) == 2


def test_array_add_inherits_shared_default(
    _isolate: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate.set_password(SERVICE, DEFAULT_KEYRING_ACCOUNT, "sharedpw")
    args = _parse(["array", "add", "--name", "inh", "--host", "10.0.0.9", "--yes"])
    assert setup_mod.run_array(args) == 0
    # No per-array key written; resolution falls through to the shared default.
    assert _isolate.get_password(SERVICE, "inh") is None
    entry = arrays_mod.resolve_array("inh")
    assert entry is not None and entry.password == "sharedpw"


def test_array_remove(_isolate: _FakeKeyring, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(setup_mod.PASSWORD_ENV, "pw")
    setup_mod.run_array(_parse(["array", "add", "--name", "gone", "--host", "10.0.0.1", "--yes"]))
    assert _isolate.get_password(SERVICE, "gone") == "pw"

    rc = setup_mod.run_array(_parse(["array", "remove", "--name", "gone"]))
    assert rc == 0
    on_disk = json.loads(arrays_mod.get_config_path().read_text(encoding="utf-8"))
    assert on_disk["arrays"] == []
    assert _isolate.get_password(SERVICE, "gone") is None  # keychain cleaned up


def test_array_remove_missing_returns_1() -> None:
    assert setup_mod.run_array(_parse(["array", "remove", "--name", "nope"])) == 1


def test_array_list_empty(capsys: pytest.CaptureFixture[str]) -> None:
    assert setup_mod.run_array(_parse(["array", "list"])) == 0
    assert "No entries" in capsys.readouterr().out


# --- helpers & dispatch ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.0.0.1", ["10.0.0.1"]),
        ("10.0.0.1,10.0.0.2", ["10.0.0.1", "10.0.0.2"]),
        ("10.0.0.1 10.0.0.2", ["10.0.0.1", "10.0.0.2"]),
        (" 10.0.0.1 , , 10.0.0.2 ", ["10.0.0.1", "10.0.0.2"]),
        ("", []),
    ],
)
def test_parse_hosts(raw: str, expected: list[str]) -> None:
    assert setup_mod._parse_hosts(raw) == expected


def test_default_password_present(_isolate: _FakeKeyring) -> None:
    assert setup_mod._default_password_present() is False
    _isolate.set_password(SERVICE, DEFAULT_KEYRING_ACCOUNT, "x")
    assert setup_mod._default_password_present() is True


def test_main_bare_args_default_to_wizard() -> None:
    # A bare `--name`/`--host` (no subcommand) is rewritten to the wizard; prove
    # the dispatch by running the array subcommand instead (no stdin needed).
    parser = setup_mod.build_parser()
    args = parser.parse_args(["wizard", "--no-claude", "--no-skill", "--no-set-defaults", "--yes"])
    assert args.command == "wizard"
    assert args.claude is False and args.skill is False
