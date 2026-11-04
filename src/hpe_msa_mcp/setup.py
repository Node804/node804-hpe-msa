"""Interactive setup wizard for hpe-msa-mcp.

Run it after ``pip install node804-hpe-msa`` (or ``uvx``):

    node804-hpe-msa-setup        # console script
    python -m hpe_msa_mcp.setup      # equivalent

It walks through the things the server needs:

1. an ``arrays.json`` entry (name + host(s)) at the standard config location;
2. the controller password, stored in the OS keychain (Windows Credential
   Manager, macOS Keychain, Linux Secret Service) — never written to disk in
   plaintext;
3. optional shared **default** credentials (one monitoring user + password reused
   by every array that has none of its own);
4. a server entry in your Claude Desktop config, pointing at this interpreter,
   with no host or password in the config (both come from 1/2); and
5. the ``hpe-msa-engineer`` skill — copied into ``~/.claude/skills`` for Claude
   Code, and zipped for upload to Claude Desktop / claude.ai.

Nothing sensitive is echoed: the password is read with ``getpass``, confirmed by
retype, and handed straight to the keychain. Some terminals (VS Code / other
integrated consoles) can't hide input and make ``getpass`` mis-read — the wizard
warns on that and on a suspiciously short value, and ``--password-stdin`` is a
terminal-agnostic alternative. The Claude Desktop config is backed up before it
is touched.

Subcommands (the wizard is the default when none is given)::

    node804-hpe-msa-setup                 # full wizard (modify-aware)
    node804-hpe-msa-setup skill           # install/upgrade the skill only
    node804-hpe-msa-setup array list      # show configured entries
    node804-hpe-msa-setup array add       # add or update an entry
    node804-hpe-msa-setup array remove --name prod

The wizard is modify-aware: when ``arrays.json`` already has entries it lists them
and lets you skip straight to the client/skill steps, so re-running it to re-wire
a client or refresh the skill does not force you to re-enter an array.

Non-interactive use (for scripted installs): pass ``--name`` / ``--host`` and
provide the password in the ``MSA_SETUP_PASSWORD`` environment variable or on
stdin with ``--password-stdin``. Add ``--yes`` to accept every default without
prompting.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.resources as ir
import json
import os
import shutil
import stat
import sys
import time
import warnings
import zipfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import keyring

from .config.arrays import (
    DEFAULT_KEYRING_ACCOUNT,
    KEYRING_SERVICE,
    ArrayEntry,
    get_config_path,
    list_array_entries,
    load_array_config,
    remove_array_entry,
    save_array_entry,
    save_defaults,
)

MODES = ("read",)
DEFAULT_SERVER_NAME = "HPE-MSA"
SKILL_NAME = "hpe-msa-engineer"
PASSWORD_ENV = "MSA_SETUP_PASSWORD"


# --- Skill install ----------------------------------------------------------


def skill_source_dir() -> Path | None:
    """Locate the bundled ``hpe-msa-engineer`` skill folder.

    Prefers the copy shipped inside the installed wheel
    (``hpe_msa_mcp/skills/hpe-msa-engineer``); falls back to the repo-root
    ``skills/hpe-msa-engineer`` tree when running from a source checkout.
    """
    try:
        packaged = ir.files("hpe_msa_mcp") / "skills" / SKILL_NAME
        if packaged.is_dir():
            return Path(str(packaged))
    except (ModuleNotFoundError, AttributeError, TypeError):
        pass
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "skills" / SKILL_NAME
        if candidate.is_dir():
            return candidate
    return None


def claude_code_skills_dir() -> Path:
    """Personal Claude Code skills directory (``~/.claude/skills``)."""
    return Path.home() / ".claude" / "skills"


def _force_rmtree(path: Path) -> None:
    """Remove a tree, clearing the read-only bit and retrying transient locks.

    On Windows a read-only file (git checkouts set this) makes ``rmtree`` raise
    ``PermissionError``, and an indexer / sync client (OneDrive) can hold a
    just-written file open for a moment. Clear the bit on error and retry a few
    times before giving up.
    """

    def _on_error(func: Any, p: str, _exc: Any) -> None:
        with suppress(OSError):
            os.chmod(p, stat.S_IWRITE)
            func(p)

    for attempt in range(5):
        try:
            shutil.rmtree(path, onexc=_on_error)
            return
        except (PermissionError, OSError):
            if attempt == 4:
                raise
            time.sleep(0.2)


def install_skill_to_dir(skills_dir: Path) -> Path:
    """Copy the bundled skill folder into ``skills_dir`` (overwriting an old copy).

    Returns the destination skill path. Raises ``FileNotFoundError`` when the
    bundled skill can't be located.
    """
    src = skill_source_dir()
    if src is None:
        raise FileNotFoundError("bundled hpe-msa-engineer skill not found")
    dest = skills_dir / SKILL_NAME
    skills_dir.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        _force_rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def zip_skill(dest_zip: Path) -> Path:
    """Write a ``hpe-msa-engineer`` skill zip for Claude Desktop / claude.ai upload.

    The archive contains a top-level ``hpe-msa-engineer/`` folder (what the Skills
    uploader expects). Returns the zip path.
    """
    src = skill_source_dir()
    if src is None:
        raise FileNotFoundError("bundled hpe-msa-engineer skill not found")
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, Path(SKILL_NAME) / path.relative_to(src))
    return dest_zip


# --- Claude Desktop config --------------------------------------------------


def claude_config_path() -> Path:
    """Return the Claude Desktop config path for this OS.

    Windows: ``%APPDATA%\\Claude``; macOS: ``~/Library/Application Support/Claude``;
    otherwise ``~/.config/Claude`` (best effort).
    """
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def build_server_entry(
    *,
    python_exe: str,
    mode: str,
    tls_verify: bool,
    audit_log: str | None = None,
) -> dict[str, Any]:
    """Build a Claude Desktop ``mcpServers`` entry for hpe-msa-mcp.

    Deliberately omits ``MSA_HOST`` / ``MSA_USERNAME`` / ``MSA_PASSWORD`` — the
    host comes from ``arrays.json`` and the password from the keychain, so neither
    lands in the Claude config.
    """
    env: dict[str, str] = {"MSA_MODE": mode}
    if not tls_verify:
        env["MSA_TLS_VERIFY"] = "false"
    if audit_log:
        env["MSA_AUDIT_LOG"] = audit_log
    return {"command": python_exe, "args": ["-m", "hpe_msa_mcp"], "env": env}


def upsert_claude_server(
    config_path: Path,
    server_name: str,
    entry: dict[str, Any],
    *,
    backup: bool = True,
) -> Path | None:
    """Add or replace ``server_name`` under ``mcpServers`` in the Claude config.

    Creates the file if missing. Backs up an existing file (timestamped) before
    writing and returns the backup path, or ``None`` when no backup was made.
    Other servers and top-level keys are preserved.
    """
    backup_path: Path | None = None
    if config_path.exists():
        if backup:
            backup_path = config_path.with_suffix(f".json.bak.{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.copy2(config_path, backup_path)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError(f"{config_path} is not a JSON object")
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {}

    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("'mcpServers' in the config is not a JSON object")
    servers[server_name] = entry

    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return backup_path


# --- Agent registry ---------------------------------------------------------
# One entry == one question in the wizard. To support another client later,
# write a ``_configure_<agent>`` function and append an AgentSetup here.


@dataclass(frozen=True)
class AgentSetup:
    """A configurable AI client the wizard can wire up to the server."""

    key: str
    label: str
    configure: Callable[[argparse.Namespace, str], None]
    flag_attr: str | None = None  # args attribute that forces on/off (None → prompt)


def _configure_claude_desktop(args: argparse.Namespace, mode: str) -> None:
    entry = build_server_entry(
        python_exe=sys.executable,
        mode=mode,
        tls_verify=not args.no_tls_verify,
        audit_log=args.audit_log,
    )
    cfg_path = Path(args.claude_config) if args.claude_config else claude_config_path()
    backup = upsert_claude_server(cfg_path, args.server_name, entry)
    print(f"[ok] Updated Claude Desktop config: {cfg_path}")
    if backup:
        print(f"     (backup saved: {backup.name})")
    print("\nServer entry written (host + password resolved from arrays.json + keychain):")
    print("  " + json.dumps({args.server_name: entry}, indent=2).replace("\n", "\n  "))
    print("Next: fully quit Claude Desktop (including the tray/menu-bar icon) and reopen it.")


AGENT_SETUPS: list[AgentSetup] = [
    AgentSetup(
        key="claude-desktop",
        label="Claude Desktop",
        configure=_configure_claude_desktop,
        flag_attr="claude",
    ),
]


# --- Prompt helpers ---------------------------------------------------------


def _prompt(label: str, default: str | None, *, assume_yes: bool) -> str:
    """Prompt for a value, returning ``default`` when the user just hits Enter."""
    suffix = f" [{default}]" if default is not None else ""
    if assume_yes and default is not None:
        print(f"{label}{suffix}: {default}")
        return default
    while True:
        raw = input(f"{label}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        print("  A value is required.")


def _prompt_yes_no(label: str, default: bool, *, assume_yes: bool) -> bool:
    d = "Y/n" if default else "y/N"
    if assume_yes:
        print(f"{label} [{d}]: {'yes' if default else 'no'}")
        return default
    raw = input(f"{label} [{d}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _parse_hosts(raw: str) -> list[str]:
    """Split a comma/space-separated host string into an ordered list.

    One array may list several management addresses (a VIP, or both controller
    IPs) for failover; the client walks them in order.
    """
    parts = [p.strip() for p in raw.replace(",", " ").split()]
    return [p for p in parts if p]


# --- Credential resolution --------------------------------------------------

# getpass under-reads in some Windows terminals (VS Code / integrated consoles),
# silently storing a 1-char password that then fails at login. A secret shorter
# than this triggers a confirm prompt so the truncation is caught at entry.
_MIN_REASONABLE_SECRET = 4


def _scripted_password(args: argparse.Namespace) -> str | None:
    """Password from a non-interactive source: ``MSA_SETUP_PASSWORD`` or stdin.

    ``--password-stdin`` reads the whole of stdin (one trailing newline stripped),
    which is terminal-agnostic and never echoed — the robust path when getpass is
    unreliable. The env var takes precedence so a scripted rollout can set either.
    """
    env = os.environ.get(PASSWORD_ENV)
    if env:
        return env
    if getattr(args, "password_stdin", False):
        raw = sys.stdin.read()
        secret = raw.rstrip("\r\n")
        return secret or None
    return None


def _read_secret_once(prompt: str) -> str:
    """Read one hidden value, surfacing getpass's insecure-fallback warning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", getpass.GetPassWarning)
        secret = getpass.getpass(prompt)
    if any(issubclass(w.category, getpass.GetPassWarning) for w in caught):
        print(
            "[warn] This terminal can't hide input securely; getpass fell back to "
            "line input and may mis-read. Prefer a plain console window, or pass "
            "--password-stdin.",
            file=sys.stderr,
        )
    return secret


def _read_secret(label: str, *, allow_empty: bool) -> str | None:
    """Prompt for a hidden secret, confirmed by retype, with a short-value guard.

    Interactive only. Re-prompts on mismatch; on a suspiciously short value (the
    getpass truncation seen in some terminals) it warns and requires an explicit
    yes before accepting, so a mangled password is caught here, not at login.
    Returns None only when ``allow_empty`` and the first entry is blank.
    """
    while True:
        first = _read_secret_once(f"{label}: ").strip()
        if not first:
            if allow_empty:
                return None
            print("  A value is required.")
            continue
        second = _read_secret_once(f"{label} (confirm): ").strip()
        if first != second:
            print("  Entries did not match — try again.")
            continue
        if len(first) < _MIN_REASONABLE_SECRET and not _prompt_yes_no(
            f"  Only {len(first)} character(s) captured — is that the whole password?",
            False,
            assume_yes=False,
        ):
            print("  Re-enter it, or use --password-stdin / a plain console window.")
            continue
        return first


def _resolve_password(
    name: str, *, assume_yes: bool, inherit_ok: bool, supplied: str | None = None
) -> str | None:
    """Get the password for ``name``: scripted source, a kept keychain entry, or prompt.

    ``supplied`` is the non-interactive password (env / stdin) resolved by the
    caller and takes precedence. When ``inherit_ok`` (shared defaults exist), an
    empty interactive entry is allowed and returns ``None`` so the entry inherits
    the shared default / env password.
    """
    if supplied:
        return supplied
    existing = keyring.get_password(KEYRING_SERVICE, name)
    if existing and _prompt_yes_no(
        f"A password for '{name}' is already in the keychain. Keep it?",
        True,
        assume_yes=assume_yes,
    ):
        return existing
    if assume_yes:
        return None
    hint = " (blank = shared default)" if inherit_ok else ""
    return _read_secret(f"MSA password for '{name}'{hint}", allow_empty=inherit_ok)


def _save_array(
    name: str, hosts: list[str], username: str, *, assume_yes: bool, supplied: str | None = None
) -> int:
    """Store the password in the keychain and add/update the arrays.json entry."""
    inherit_ok = _default_password_present()
    password = _resolve_password(
        name, assume_yes=assume_yes, inherit_ok=inherit_ok, supplied=supplied
    )
    if password is None and not inherit_ok:
        print(
            f"ERROR: no password for '{name}'. Set {PASSWORD_ENV} or pass "
            "--password-stdin for non-interactive use, set shared default "
            "credentials first, or run interactively.",
            file=sys.stderr,
        )
        return 2
    save_array_entry(ArrayEntry(name=name, hosts=hosts, username=username, password=password))
    if password is not None:
        print(f"[ok] Stored password in keychain (service '{KEYRING_SERVICE}', entry '{name}').")
    else:
        print("[ok] No per-array password stored; entry inherits the shared default.")
    target = ", ".join(hosts)
    print(f"[ok] Updated {get_config_path()} (name '{name}', host(s) {target}; no password).")
    return 0


def _default_password_present() -> bool:
    """True when a shared default password is already in the keychain or env."""
    with_env = bool(os.environ.get("MSA_PASSWORD"))
    try:
        in_keychain = bool(keyring.get_password(KEYRING_SERVICE, DEFAULT_KEYRING_ACCOUNT))
    except Exception:
        in_keychain = False
    return with_env or in_keychain


def _maybe_set_defaults(args: argparse.Namespace, *, supplied: str | None = None) -> None:
    """Offer to set shared default credentials (one user+password for all arrays)."""
    if args.yes and not args.set_defaults:
        return
    want = args.set_defaults
    if want is None:
        want = _prompt_yes_no(
            "\nSet shared DEFAULT credentials (one monitoring user+password reused "
            "by every array)?",
            False,
            assume_yes=args.yes,
        )
    if not want:
        return
    username = _prompt("Default username", "monitor", assume_yes=args.yes)
    password = supplied
    if password is None and not args.yes:
        password = _read_secret("Default password", allow_empty=True)
    if not password:
        print("[warn] No default password provided; skipping defaults.", file=sys.stderr)
        return
    save_defaults(username=username, password=password)
    print(
        f"[ok] Stored default credentials (user '{username}'; password in keychain "
        f"account '{DEFAULT_KEYRING_ACCOUNT}')."
    )


def _print_entries(entries: list[dict[str, object]]) -> None:
    for e in entries:
        hosts = e.get("hosts") or []
        target = ", ".join(hosts) if isinstance(hosts, list) else str(hosts)
        print(f"  - {e['name']}  ({target})  user={e.get('username')}")


def _do_skill_install(
    *, skills_dir: Path, do_copy: bool = True, do_zip: bool = True, zip_dir: Path | None = None
) -> int:
    """Install/upgrade the skill: copy into ``skills_dir`` and/or write an upload zip."""
    if skill_source_dir() is None:
        print(f"[warn] bundled {SKILL_NAME} skill not found; nothing to install.", file=sys.stderr)
        return 1
    if do_copy:
        dest = install_skill_to_dir(skills_dir)
        print(f"[ok] Installed/updated skill for Claude Code: {dest}")
    if do_zip:
        zip_path = (zip_dir or Path.cwd()) / f"{SKILL_NAME}-skill.zip"
        zip_skill(zip_path)
        print(f"[ok] Wrote {zip_path}")
        print(
            "     For Claude Desktop / claude.ai, upload that zip under "
            "Settings → Capabilities → Skills."
        )
    return 0


# --- Wizard -----------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    print("hpe-msa-mcp setup\n" + "=" * 17)

    load_array_config()
    existing = list_array_entries()
    if existing:
        print(f"\nExisting entries in {get_config_path()}:")
        _print_entries(existing)

    # One non-interactive password source (env / stdin) for this run, read once.
    supplied = _scripted_password(args)

    # Shared defaults first, so a subsequent array can inherit them.
    _maybe_set_defaults(args, supplied=supplied)

    # Add/update an array entry. Forced when --name/--host are given or nothing is
    # configured yet; otherwise opt-in, so a re-run can go straight to the
    # client/skill steps without re-entering an array.
    if args.name or args.host:
        do_array = True
    elif existing:
        do_array = _prompt_yes_no("\nAdd or update an array entry?", False, assume_yes=args.yes)
    else:
        do_array = True

    if do_array:
        name = args.name or _prompt("Array name", "msa", assume_yes=args.yes)
        host_raw = args.host or _prompt(
            "Host(s) — VIP or both controller IPs, comma-separated", None, assume_yes=args.yes
        )
        hosts = _parse_hosts(host_raw)
        if not hosts:
            print("ERROR: at least one host is required.", file=sys.stderr)
            return 2
        username = args.username or _prompt("Username", "monitor", assume_yes=args.yes)
        rc = _save_array(name, hosts, username, assume_yes=args.yes, supplied=supplied)
        if rc:
            return rc

    mode = args.mode or "read"
    if mode not in MODES:
        print(f"ERROR: mode must be one of {', '.join(MODES)}.", file=sys.stderr)
        return 2

    # --- AI agents ----------------------------------------------------------
    for agent in AGENT_SETUPS:
        forced = getattr(args, agent.flag_attr) if agent.flag_attr else None
        want = (
            forced
            if forced is not None
            else _prompt_yes_no(f"Set up {agent.label}?", True, assume_yes=args.yes)
        )
        if want:
            agent.configure(args, mode)

    # --- hpe-msa-engineer skill --------------------------------------------
    want_skill = args.skill
    if want_skill is None:
        want_skill = _prompt_yes_no(f"\nInstall the {SKILL_NAME} skill?", True, assume_yes=args.yes)
    if want_skill:
        skills_dir = Path(args.skills_dir) if args.skills_dir else claude_code_skills_dir()
        _do_skill_install(skills_dir=skills_dir)

    print('\nVerify in your client by asking: "Is my MSA array healthy?"')
    return 0


def run_array(args: argparse.Namespace) -> int:
    """``array`` subcommand: list / add / remove entries in arrays.json."""
    load_array_config()
    cmd = args.array_command or "list"

    if cmd == "list":
        entries = list_array_entries()
        if not entries:
            print(f"No entries in {get_config_path()}.")
            return 0
        print(f"Entries in {get_config_path()}:")
        _print_entries(entries)
        return 0

    if cmd == "add":
        name = args.name or _prompt("Array name", "msa", assume_yes=args.yes)
        host_raw = args.host or _prompt(
            "Host(s) — VIP or both controller IPs, comma-separated", None, assume_yes=args.yes
        )
        hosts = _parse_hosts(host_raw)
        if not hosts:
            print("ERROR: at least one host is required.", file=sys.stderr)
            return 2
        username = args.username or _prompt("Username", "monitor", assume_yes=args.yes)
        return _save_array(
            name, hosts, username, assume_yes=args.yes, supplied=_scripted_password(args)
        )

    if cmd == "remove":
        if remove_array_entry(args.name):
            print(f"[ok] Removed '{args.name}' from {get_config_path()} and the keychain.")
            return 0
        print(f"[warn] No entry named '{args.name}'.", file=sys.stderr)
        return 1

    return 2


def run_skill(args: argparse.Namespace) -> int:
    """``skill`` subcommand: install or upgrade the hpe-msa-engineer skill."""
    skills_dir = Path(args.skills_dir) if args.skills_dir else claude_code_skills_dir()
    zip_dir = Path(args.zip_dir) if args.zip_dir else None
    return _do_skill_install(
        skills_dir=skills_dir,
        do_copy=not args.no_copy,
        do_zip=not args.no_zip,
        zip_dir=zip_dir,
    )


def _add_wizard_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--name", help="Array entry name (keychain account).")
    p.add_argument("--host", help="Array host(s): VIP or comma-separated controller IPs.")
    p.add_argument("--username", help="Controller username for this array.")
    p.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin (terminal-agnostic; avoids getpass).",
    )
    p.add_argument(
        "--mode", choices=MODES, help="Permission mode (read-only server; default: read)."
    )
    p.add_argument(
        "--server-name",
        default=DEFAULT_SERVER_NAME,
        help=f"Name of the server entry in the Claude config (default: {DEFAULT_SERVER_NAME}).",
    )
    p.add_argument("--audit-log", help="Path for MSA_AUDIT_LOG (optional).")
    p.add_argument("--claude-config", help="Override the Claude Desktop config path.")
    p.add_argument(
        "--no-tls-verify",
        action="store_true",
        help="Set MSA_TLS_VERIFY=false (self-signed cert; prefer MSA_TLS_CA instead).",
    )
    defaults = p.add_mutually_exclusive_group()
    defaults.add_argument(
        "--set-defaults",
        dest="set_defaults",
        action="store_true",
        default=None,
        help="Set shared default credentials without prompting.",
    )
    defaults.add_argument(
        "--no-set-defaults",
        dest="set_defaults",
        action="store_false",
        help="Skip the shared-default-credentials step.",
    )
    claude = p.add_mutually_exclusive_group()
    claude.add_argument(
        "--claude",
        dest="claude",
        action="store_true",
        default=None,
        help="Configure Claude Desktop without prompting.",
    )
    claude.add_argument(
        "--no-claude", dest="claude", action="store_false", help="Skip the Claude Desktop step."
    )
    skill = p.add_mutually_exclusive_group()
    skill.add_argument(
        "--skill",
        dest="skill",
        action="store_true",
        default=None,
        help="Install the hpe-msa-engineer skill without prompting.",
    )
    skill.add_argument(
        "--no-skill",
        dest="skill",
        action="store_false",
        help="Skip installing the hpe-msa-engineer skill.",
    )
    p.add_argument(
        "--skills-dir",
        help="Override the Claude Code skills directory (default: ~/.claude/skills).",
    )
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help=f"Accept defaults without prompting (password must come from {PASSWORD_ENV}).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="node804-hpe-msa-setup",
        description=(
            "Set up and manage hpe-msa-mcp: arrays.json, the keychain password, "
            "AI-client config, and the hpe-msa-engineer skill."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    _add_wizard_args(sub.add_parser("wizard", help="Full interactive setup (default)."))

    sk = sub.add_parser("skill", help="Install or upgrade the hpe-msa-engineer skill only.")
    sk.add_argument(
        "--skills-dir", help="Claude Code skills directory (default: ~/.claude/skills)."
    )
    sk.add_argument("--zip-dir", help="Directory for the Desktop upload zip (default: cwd).")
    sk.add_argument("--no-copy", action="store_true", help="Do not copy into the skills directory.")
    sk.add_argument("--no-zip", action="store_true", help="Do not write the Desktop upload zip.")

    ar = sub.add_parser("array", help="Manage arrays.json entries.")
    arsub = ar.add_subparsers(dest="array_command")
    arsub.add_parser("list", help="List configured entries.")
    ar_add = arsub.add_parser("add", help="Add or update an entry (password stored in keychain).")
    ar_add.add_argument("--name", help="Entry name (keychain account).")
    ar_add.add_argument("--host", help="Array host(s): VIP or comma-separated controller IPs.")
    ar_add.add_argument("--username", help="Controller username for this array.")
    ar_add.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin (terminal-agnostic; avoids getpass).",
    )
    ar_add.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help=f"Non-interactive; password from {PASSWORD_ENV} or --password-stdin.",
    )
    ar_rm = arsub.add_parser("remove", help="Remove an entry and its keychain password.")
    ar_rm.add_argument("--name", required=True, help="Entry name to remove.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()

    # Default to the wizard when no subcommand is given, so a bare
    # `node804-hpe-msa-setup` and `... --name x` both run the wizard — but let
    # top-level `-h/--help` through so the subcommands stay discoverable.
    raw = list(sys.argv[1:] if argv is None else argv)
    known = {"wizard", "skill", "array"}
    if raw and raw[0] not in known and raw[0] not in ("-h", "--help"):
        raw = ["wizard", *raw]
    elif not raw:
        raw = ["wizard"]
    args = parser.parse_args(raw)

    try:
        if args.command == "skill":
            return run_skill(args)
        if args.command == "array":
            return run_array(args)
        return run(args)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
