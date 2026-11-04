"""Tests for the array registry (config/arrays.py).

Keyring is replaced with an in-memory fake so tests never touch the real OS
keychain, and the config path is redirected to a tmp file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hpe_msa_mcp.config import arrays as arrays_mod
from hpe_msa_mcp.config.arrays import (
    DEFAULT_KEYRING_ACCOUNT,
    ArrayEntry,
    get_defaults,
    is_multi_array,
    list_array_entries,
    load_array_config,
    resolve_array,
    save_array_entry,
    save_defaults,
)

SERVICE = "hpe-msa-mcp"


class _FakeKeyring:
    """Minimal in-memory keyring stand-in: (service, account) -> password."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.store[(service, account)] = password


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeKeyring:
    """Redirect keyring to an in-memory fake and config to a tmp path."""
    fake = _FakeKeyring()
    monkeypatch.setattr(arrays_mod, "keyring", fake)
    monkeypatch.setenv("MSA_ARRAYS_CONFIG", str(tmp_path / "arrays.json"))
    for var in ("MSA_HOST", "MSA_USERNAME", "MSA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    arrays_mod._entries = []
    arrays_mod._defaults_username = None
    return fake


def _config_path() -> Path:
    return arrays_mod.get_config_path()


def _write(config: dict) -> None:
    _config_path().write_text(json.dumps(config), encoding="utf-8")


# --- Env fallback -----------------------------------------------------------


def test_env_fallback_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSA_HOST", "https://10.0.0.10/")
    monkeypatch.setenv("MSA_USERNAME", "manage")
    monkeypatch.setenv("MSA_PASSWORD", "secret")
    load_array_config()
    entry = resolve_array()
    assert entry is not None
    assert entry.name == "env"
    assert entry.hosts == ["10.0.0.10"]
    assert entry.host == "10.0.0.10"
    assert entry.username == "manage"


def test_no_config_no_env_resolves_none() -> None:
    load_array_config()
    assert resolve_array() is None


def test_empty_file_does_not_crash(_isolate: _FakeKeyring) -> None:
    # A zero-byte arrays.json (a common half-created state) must be tolerated,
    # not crash the server at startup.
    _config_path().write_text("", encoding="utf-8")
    load_array_config()  # must not raise
    assert resolve_array() is None
    assert list_array_entries() == []


def test_malformed_file_does_not_crash(_isolate: _FakeKeyring) -> None:
    _config_path().write_text("{not valid json", encoding="utf-8")
    load_array_config()  # must not raise
    assert resolve_array() is None


# --- File load + keychain migration ----------------------------------------


def test_plaintext_password_migrated_and_stripped(_isolate: _FakeKeyring) -> None:
    _write(
        {"arrays": [{"name": "san-a", "host": "10.0.0.10", "username": "manage", "password": "pw"}]}
    )
    load_array_config()

    assert _isolate.get_password(SERVICE, "san-a") == "pw"
    on_disk = json.loads(_config_path().read_text(encoding="utf-8"))
    assert "password" not in on_disk["arrays"][0]
    entry = resolve_array("san-a")
    assert entry is not None and entry.password == "pw"


def test_password_read_from_keychain(_isolate: _FakeKeyring) -> None:
    _isolate.set_password(SERVICE, "san-b", "kcpw")
    _write({"arrays": [{"name": "san-b", "host": "10.0.0.20", "username": "manage"}]})
    load_array_config()
    entry = resolve_array("san-b")
    assert entry is not None and entry.password == "kcpw"


def test_entry_without_password_is_skipped(_isolate: _FakeKeyring) -> None:
    _write({"arrays": [{"name": "orphan", "host": "10.0.0.99", "username": "manage"}]})
    load_array_config()
    assert resolve_array("orphan") is None


# --- Shared defaults --------------------------------------------------------


def test_default_username_and_password_inherited(_isolate: _FakeKeyring) -> None:
    # Shared password in the keychain default account; username in defaults block.
    _isolate.set_password(SERVICE, DEFAULT_KEYRING_ACCOUNT, "sharedpw")
    _write(
        {
            "defaults": {"username": "monitor"},
            "arrays": [
                {"name": "a", "host": "10.0.0.1"},
                {"name": "b", "host": "10.0.0.2"},
            ],
        }
    )
    load_array_config()

    for name in ("a", "b"):
        entry = resolve_array(name)
        assert entry is not None
        assert entry.username == "monitor"
        assert entry.password == "sharedpw"
    assert get_defaults() == {"username": "monitor", "password_set": True}


def test_per_array_overrides_default(_isolate: _FakeKeyring) -> None:
    _isolate.set_password(SERVICE, DEFAULT_KEYRING_ACCOUNT, "sharedpw")
    _isolate.set_password(SERVICE, "special", "ownpw")
    _write(
        {
            "defaults": {"username": "monitor"},
            "arrays": [
                {"name": "normal", "host": "10.0.0.1"},
                {"name": "special", "host": "10.0.0.2", "username": "admin"},
            ],
        }
    )
    load_array_config()

    normal = resolve_array("normal")
    special = resolve_array("special")
    assert normal is not None and normal.username == "monitor" and normal.password == "sharedpw"
    assert special is not None and special.username == "admin" and special.password == "ownpw"


def test_default_plaintext_password_migrated(_isolate: _FakeKeyring) -> None:
    _write(
        {
            "defaults": {"username": "monitor", "password": "sharedpw"},
            "arrays": [{"name": "a", "host": "10.0.0.1"}],
        }
    )
    load_array_config()

    # Default password migrated to the keychain default account, stripped from file.
    assert _isolate.get_password(SERVICE, DEFAULT_KEYRING_ACCOUNT) == "sharedpw"
    on_disk = json.loads(_config_path().read_text(encoding="utf-8"))
    assert "password" not in on_disk.get("defaults", {})
    entry = resolve_array("a")
    assert entry is not None and entry.password == "sharedpw"


def test_env_password_used_as_default(
    _isolate: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MSA_USERNAME", "monitor")
    monkeypatch.setenv("MSA_PASSWORD", "envpw")
    _write({"arrays": [{"name": "a", "host": "10.0.0.1"}]})
    load_array_config()
    entry = resolve_array("a")
    assert entry is not None
    assert entry.username == "monitor"
    assert entry.password == "envpw"


def test_save_defaults_writes_username_and_keychain_password(_isolate: _FakeKeyring) -> None:
    _write({"arrays": [{"name": "a", "host": "10.0.0.1"}]})
    load_array_config()
    save_defaults(username="monitor", password="sharedpw")

    on_disk = json.loads(_config_path().read_text(encoding="utf-8"))
    assert on_disk["defaults"] == {"username": "monitor"}  # password never in file
    assert _isolate.get_password(SERVICE, DEFAULT_KEYRING_ACCOUNT) == "sharedpw"
    # Now the previously-orphan array resolves via the shared default.
    entry = resolve_array("a")
    assert entry is not None and entry.username == "monitor" and entry.password == "sharedpw"


# --- Failover hosts ---------------------------------------------------------


def test_hosts_list_preserved_and_deduped(_isolate: _FakeKeyring) -> None:
    _isolate.set_password(SERVICE, "a", "pw")
    _write(
        {
            "arrays": [
                {
                    "name": "a",
                    "host": "10.0.0.1",
                    "hosts": ["10.0.0.1", "https://10.0.0.2/"],
                    "username": "manage",
                }
            ]
        }
    )
    load_array_config()
    entry = resolve_array("a")
    assert entry is not None
    # host + hosts merged, normalized, de-duplicated, order preserved.
    assert entry.hosts == ["10.0.0.1", "10.0.0.2"]
    assert entry.host == "10.0.0.1"


# --- Multi-array semantics --------------------------------------------------


def test_multi_array_requires_name(_isolate: _FakeKeyring) -> None:
    _isolate.set_password(SERVICE, "a", "p1")
    _isolate.set_password(SERVICE, "b", "p2")
    _write(
        {
            "arrays": [
                {"name": "a", "host": "10.0.0.1", "username": "manage"},
                {"name": "b", "host": "10.0.0.2", "username": "manage"},
            ]
        }
    )
    load_array_config()
    assert is_multi_array() is True
    assert resolve_array() is None
    assert resolve_array("a") is not None
    assert resolve_array("missing") is None


# --- save_array_entry -------------------------------------------------------


def test_save_entry_persists_without_password(_isolate: _FakeKeyring) -> None:
    save_array_entry(ArrayEntry(name="new", hosts=["10.0.0.5"], username="manage", password="pw"))
    on_disk = json.loads(_config_path().read_text(encoding="utf-8"))
    record = on_disk["arrays"][0]
    assert record == {"name": "new", "host": "10.0.0.5", "username": "manage"}
    assert _isolate.get_password(SERVICE, "new") == "pw"
    assert resolve_array("new") is not None


def test_save_entry_multi_host_uses_hosts_key(_isolate: _FakeKeyring) -> None:
    save_array_entry(
        ArrayEntry(name="ha", hosts=["10.0.0.5", "10.0.0.6"], username="manage", password="pw")
    )
    record = json.loads(_config_path().read_text(encoding="utf-8"))["arrays"][0]
    assert record == {"name": "ha", "hosts": ["10.0.0.5", "10.0.0.6"], "username": "manage"}


def test_save_entry_inheriting_default_password(_isolate: _FakeKeyring) -> None:
    _isolate.set_password(SERVICE, DEFAULT_KEYRING_ACCOUNT, "sharedpw")
    # Entry with no password of its own inherits the shared default.
    save_array_entry(ArrayEntry(name="inh", hosts=["10.0.0.7"], username="monitor", password=None))
    assert _isolate.get_password(SERVICE, "inh") is None  # no per-array key written
    entry = resolve_array("inh")
    assert entry is not None and entry.password == "sharedpw"


def test_list_entries_never_exposes_password(_isolate: _FakeKeyring) -> None:
    save_array_entry(ArrayEntry(name="x", hosts=["10.0.0.5"], username="manage", password="pw"))
    for record in list_array_entries():
        assert "password" not in record
        assert "hosts" in record


# --- validation -------------------------------------------------------------


def test_bad_name_rejected() -> None:
    with pytest.raises(ValueError):
        ArrayEntry(name="bad/name", hosts=["10.0.0.5"], username="manage", password="pw")


def test_reserved_name_rejected() -> None:
    with pytest.raises(ValueError):
        ArrayEntry(name=DEFAULT_KEYRING_ACCOUNT, hosts=["10.0.0.5"], username="m", password="pw")


def test_host_normalized() -> None:
    entry = ArrayEntry(name="n", hosts=["HTTPS://10.0.0.5:443/"], username="manage", password="pw")
    assert entry.hosts == ["10.0.0.5:443"]
