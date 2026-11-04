"""Multi-array registry for hpe-msa-mcp.

Two configuration sources, in priority order:

1. ``arrays.json`` (location: ``$MSA_ARRAYS_CONFIG`` or
   ``~/.config/hpe-msa-mcp/arrays.json``) — a list of named MSA targets plus an
   optional ``defaults`` block. The controller **password** lives in the OS
   keychain (via ``keyring``); the JSON file holds only non-secret fields.
   Plaintext ``password`` fields (per-array or in ``defaults``) are auto-migrated
   to the keychain on first load and the JSON is rewritten without them.
2. ``MSA_HOST`` + ``MSA_USERNAME`` + ``MSA_PASSWORD`` env vars —
   single-array fallback used when no JSON file is present.

One entry == one array (not one controller). An MSA array is a single logical
management target: logging into either controller returns the whole array. To
survive a controller's management port going down, either point ``host`` at the
array's management VIP, or list both controller IPs in ``hosts`` — the client
tries them in order and uses the first that answers.

**Shared credentials.** Many shops run the same monitoring user + password on
every array. Set them once in ``defaults`` (username) + the keychain default
account (password), then list just ``{name, host}`` per array. Any entry can
override ``username`` (and its own keychain password).

Resolution per array:
  - username: entry ``username`` → ``defaults.username`` → ``MSA_USERNAME``
  - password: keychain[name] → keychain[default] → plaintext file → ``MSA_PASSWORD``

The file is always written with mode ``0o600`` on POSIX (best-effort on
Windows) so plaintext fallback passwords aren't world-readable.

Public API:
    load_array_config()             # initialize on startup
    resolve_array(name=None)        # look up a configured target
    is_multi_array()                # True when >1 entry configured
    list_array_entries()            # non-secret view (never passwords)
    save_array_entry(entry)         # write a new/updated entry
    save_defaults(...)              # write shared default username/password
    get_defaults()                  # non-secret defaults view
"""

from __future__ import annotations

import json
import os
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Final

import keyring
from pydantic import BaseModel, ConfigDict, Field, field_validator

KEYRING_SERVICE: Final = "hpe-msa-mcp"
"""Service name used for OS keychain entries."""

DEFAULT_KEYRING_ACCOUNT: Final = "__default__"
"""Keychain account holding the shared default password (used by any array
entry that has no password of its own). Reserved — cannot be an array name."""

_AVAILABILITY_ACCOUNT: Final = "__availability_test__"
"""Account used only to probe whether the keychain backend works."""

# Array names are used as keychain account keys and echoed into log lines;
# keep them to a safe, predictable charset.
_ARRAY_NAME_REGEX = re.compile(r"^[A-Za-z0-9._\- ]+$")


class ArrayEntry(BaseModel):
    """In-memory representation of a resolved MSA target.

    ``hosts`` is the failover-ordered list of management addresses for the one
    array (usually one VIP, or the two controller IPs). ``host`` is a
    convenience alias for the primary (``hosts[0]``).
    """

    name: str = Field(min_length=1, max_length=63)
    hosts: list[str] = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str | None = None

    model_config = ConfigDict(frozen=True)

    @property
    def host(self) -> str:
        """Primary management address (first in the failover list)."""
        return self.hosts[0]

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _ARRAY_NAME_REGEX.match(v):
            raise ValueError(
                "Array name must contain only letters, digits, dot, underscore, hyphen, or space."
            )
        if v == DEFAULT_KEYRING_ACCOUNT:
            raise ValueError(
                f"'{DEFAULT_KEYRING_ACCOUNT}' is reserved and cannot be an array name."
            )
        return v

    @field_validator("hosts")
    @classmethod
    def _normalize_hosts(cls, v: list[str]) -> list[str]:
        cleaned = [_normalize_host_str(h) for h in v]
        cleaned = [h for h in cleaned if h]
        if not cleaned:
            raise ValueError("Array must have at least one non-empty host.")
        return cleaned


class _FileDefaults(BaseModel):
    """Optional shared defaults block in arrays.json."""

    username: str | None = None
    password: str | None = None  # legacy; auto-migrated to keychain default account

    model_config = ConfigDict(extra="ignore")


class _FileEntry(BaseModel):
    """Schema of a single entry as stored in arrays.json on disk.

    ``host`` and ``hosts`` are both accepted; ``host`` is shorthand for a
    single-address ``hosts``. ``username``/``password`` are optional — when
    omitted the entry inherits the shared defaults.
    """

    name: str
    host: str | None = None
    hosts: list[str] | None = None
    username: str | None = None
    password: str | None = None  # legacy; auto-migrated to keychain on load

    model_config = ConfigDict(extra="ignore")

    def host_list(self) -> list[str]:
        """Merge ``host`` + ``hosts`` into one ordered, de-duplicated list."""
        raw: list[str] = []
        if self.host:
            raw.append(self.host)
        if self.hosts:
            raw.extend(self.hosts)
        seen: set[str] = set()
        out: list[str] = []
        for h in (_normalize_host_str(x) for x in raw):
            if h and h not in seen:
                seen.add(h)
                out.append(h)
        return out


class _FileSchema(BaseModel):
    defaults: _FileDefaults = Field(default_factory=_FileDefaults)
    arrays: list[_FileEntry] = Field(default_factory=list)


# --- Module-level state -----------------------------------------------------
# Populated by load_array_config() at startup. The registry is global because
# tools resolve targets ad hoc and a singleton avoids threading it through
# every call site.

_entries: list[ArrayEntry] = []
"""Currently loaded entries with password populated."""

_defaults_username: str | None = None
"""Shared default username from arrays.json ``defaults`` (or None)."""


def get_config_path() -> Path:
    """Resolve the arrays.json path from env or the default location."""
    raw = os.environ.get("MSA_ARRAYS_CONFIG", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".config" / "hpe-msa-mcp" / "arrays.json"


def load_array_config() -> None:
    """Initialize the registry from disk + keychain + env fallback.

    Called once at server startup. Idempotent: calling twice replaces the
    existing state with a fresh load.
    """
    global _entries, _defaults_username

    path = get_config_path()
    raw_text: str | None = None
    with suppress(OSError):
        raw_text = path.read_text(encoding="utf-8")

    if raw_text is None or not raw_text.strip():
        # No file, or an empty file (a common half-created state). Treat as no
        # config rather than crashing the server at startup.
        _entries = []
        _defaults_username = None
        return

    try:
        parsed = _FileSchema.model_validate(json.loads(raw_text))
    except (json.JSONDecodeError, ValueError) as err:
        # A malformed config file must not take the whole server down. Warn and
        # fall back to the env single-array path (empty registry).
        from .._redact import redact_exception

        print(
            f"[hpe-msa-mcp] WARNING: could not parse {path} ({redact_exception(err)}). "
            "Ignoring it; using env fallback if set.",
            file=sys.stderr,
        )
        _entries = []
        _defaults_username = None
        return
    _defaults_username = (parsed.defaults.username or "").strip() or None
    default_plaintext_pw = parsed.defaults.password

    file_entries = list(parsed.arrays)

    # Auto-migrate plaintext passwords (per-array + default) to the keychain,
    # then rewrite the file without them.
    per_array_to_migrate = [e for e in file_entries if e.password]
    if (per_array_to_migrate or default_plaintext_pw) and _keyring_available():
        try:
            for e in per_array_to_migrate:
                assert e.password is not None
                keyring.set_password(KEYRING_SERVICE, e.name, e.password)
            if default_plaintext_pw:
                keyring.set_password(KEYRING_SERVICE, DEFAULT_KEYRING_ACCOUNT, default_plaintext_pw)
            migrated = len(per_array_to_migrate) + (1 if default_plaintext_pw else 0)
            # Null the plaintext BEFORE rewriting so the on-disk file carries no
            # passwords — the keychain is now authoritative.
            for e in file_entries:
                e.password = None
            default_plaintext_pw = None
            _write_config_file(path, _defaults_username, file_entries)
            print(
                f"[hpe-msa-mcp] Migrated {migrated} password(s) to system keychain",
                file=sys.stderr,
            )
        except Exception as err:
            from .._redact import redact_exception

            print(
                f"[hpe-msa-mcp] WARNING: keychain migration failed "
                f"({redact_exception(err)}). Passwords remain in plaintext.",
                file=sys.stderr,
            )

    resolved: list[ArrayEntry] = []
    for fe in file_entries:
        hosts = fe.host_list()
        if not hosts:
            print(
                f"[hpe-msa-mcp] WARNING: array '{fe.name}' has no host/hosts — skipping.",
                file=sys.stderr,
            )
            continue

        username = _resolve_username(fe.username)
        if not username:
            print(
                f"[hpe-msa-mcp] WARNING: no username for array '{fe.name}' (and no default) "
                "— skipping until one is set.",
                file=sys.stderr,
            )
            continue

        password = _resolve_password(fe.name, fe.password, default_plaintext_pw)
        if not password:
            print(
                f"[hpe-msa-mcp] WARNING: no password for array '{fe.name}' (per-array, default, "
                "or MSA_PASSWORD) — skipping until one is set.",
                file=sys.stderr,
            )
            continue

        resolved.append(ArrayEntry(name=fe.name, hosts=hosts, username=username, password=password))

    _entries = resolved


def resolve_array(name: str | None = None) -> ArrayEntry | None:
    """Resolve a configured MSA target.

    Resolution order:
      1. If ``name`` is given, return that configured entry (or None).
      2. Else if exactly one entry is configured, return it.
      3. Else fall back to ``MSA_HOST`` + ``MSA_USERNAME`` + ``MSA_PASSWORD``.
      4. Else return None.

    Multi-array mode (>1 entry) requires the caller to specify ``name`` — we
    deliberately don't pick a default to avoid silently targeting the wrong
    array.
    """
    if name is not None:
        for e in _entries:
            if e.name == name:
                return e
        return None

    if len(_entries) == 1:
        return _entries[0]

    if len(_entries) > 1:
        return None

    # No JSON config — env-var fallback for single-array use.
    host = _normalize_host_str(os.environ.get("MSA_HOST", ""))
    username = os.environ.get("MSA_USERNAME", "").strip()
    password = os.environ.get("MSA_PASSWORD", "")
    if host and username and password:
        return ArrayEntry(name="env", hosts=[host], username=username, password=password)
    return None


def is_multi_array() -> bool:
    """True when multiple array entries are configured.

    When True, every tool that talks to an array must take an explicit
    ``array: str`` argument — there's no default to pick.
    """
    return len(_entries) > 1


def list_array_entries() -> list[dict[str, object]]:
    """Return a password-free view of every configured entry."""
    return [{"name": e.name, "hosts": list(e.hosts), "username": e.username} for e in _entries]


def get_defaults() -> dict[str, object]:
    """Non-secret view of shared defaults, for server_status / diagnostics."""
    return {
        "username": _defaults_username,
        "password_set": _default_password_available(),
    }


def save_array_entry(entry: ArrayEntry) -> None:
    """Persist a new or updated entry to disk + keychain.

    A password on the entry is stored in the keychain (or plaintext in the file
    with a warning when the keychain is unavailable). When ``entry.password`` is
    None the entry relies on the shared default / env password — nothing is
    written to the keychain for it. The file is written mode ``0o600``.
    """
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    parsed = _read_file_schema(path)
    store_plaintext = False
    if entry.password:
        if _keyring_available():
            keyring.set_password(KEYRING_SERVICE, entry.name, entry.password)
        else:
            print(
                "[hpe-msa-mcp] WARNING: keychain unavailable; storing password in plaintext "
                f"in {path}. File will be chmod'd to 0o600.",
                file=sys.stderr,
            )
            store_plaintext = True

    new_record = _FileEntry(
        name=entry.name,
        hosts=list(entry.hosts),
        username=entry.username,
        password=entry.password if store_plaintext else None,
    )
    replaced = False
    for i, existing in enumerate(parsed.arrays):
        if existing.name == entry.name:
            parsed.arrays[i] = new_record
            replaced = True
            break
    if not replaced:
        parsed.arrays.append(new_record)

    _write_config_file(
        path, parsed.defaults.username, parsed.arrays, defaults_extra=parsed.defaults
    )

    # Reload so the new entry is immediately resolvable with fully-resolved
    # credentials (a password inherited from the shared default is filled in
    # here rather than left None on the in-memory entry).
    load_array_config()


def save_defaults(*, username: str | None = None, password: str | None = None) -> None:
    """Persist shared default credentials.

    ``username`` is written to the ``defaults`` block in arrays.json (non-secret).
    ``password`` is stored in the keychain default account (or plaintext in the
    file's ``defaults`` block, with a warning, when the keychain is unavailable).
    Either argument may be omitted to leave that value unchanged.
    """
    global _defaults_username

    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    parsed = _read_file_schema(path)

    if username is not None:
        parsed.defaults.username = username.strip() or None

    default_plaintext: str | None = None
    if password is not None:
        if _keyring_available():
            keyring.set_password(KEYRING_SERVICE, DEFAULT_KEYRING_ACCOUNT, password)
        else:
            print(
                "[hpe-msa-mcp] WARNING: keychain unavailable; storing default password in "
                f"plaintext in {path}. File will be chmod'd to 0o600.",
                file=sys.stderr,
            )
            default_plaintext = password

    parsed.defaults.password = default_plaintext
    _write_config_file(
        path, parsed.defaults.username, parsed.arrays, defaults_extra=parsed.defaults
    )

    # Refresh in-memory state so subsequent resolves see the new defaults.
    _defaults_username = parsed.defaults.username
    load_array_config()


def remove_array_entry(name: str) -> bool:
    """Remove an entry from arrays.json and delete its per-array keychain password.

    Returns True when an entry named ``name`` was found and removed, False when no
    such entry existed. The keychain password is best-effort deleted either way.
    The shared default account is never touched (it may back other entries).
    """
    path = get_config_path()
    removed = False
    with suppress(OSError, json.JSONDecodeError):
        config = json.loads(path.read_text(encoding="utf-8"))
        arrays = config.get("arrays", [])
        if isinstance(arrays, list):
            kept = [e for e in arrays if e.get("name") != name]
            if len(kept) != len(arrays):
                removed = True
                config["arrays"] = kept
                _write_restricted(path, json.dumps(config, indent=2) + "\n")

    if _keyring_available():
        with suppress(Exception):
            keyring.delete_password(KEYRING_SERVICE, name)

    global _entries
    _entries = [e for e in _entries if e.name != name]
    return removed


# --- Internals --------------------------------------------------------------


def _resolve_username(entry_username: str | None) -> str | None:
    """Per-array username → shared default → MSA_USERNAME env."""
    if entry_username and entry_username.strip():
        return entry_username.strip()
    if _defaults_username:
        return _defaults_username
    env = os.environ.get("MSA_USERNAME", "").strip()
    return env or None


def _resolve_password(
    name: str, entry_plaintext: str | None, default_plaintext: str | None
) -> str | None:
    """keychain[name] → keychain[default] → plaintext(entry→default) → MSA_PASSWORD."""
    if _keyring_available():
        per_array = keyring.get_password(KEYRING_SERVICE, name)
        if per_array:
            return per_array
        shared = keyring.get_password(KEYRING_SERVICE, DEFAULT_KEYRING_ACCOUNT)
        if shared:
            return shared
    if entry_plaintext:
        return entry_plaintext
    if default_plaintext:
        return default_plaintext
    env = os.environ.get("MSA_PASSWORD", "")
    return env or None


def _default_password_available() -> bool:
    """True when a shared default password can be resolved (keychain or env)."""
    if _keyring_available() and keyring.get_password(KEYRING_SERVICE, DEFAULT_KEYRING_ACCOUNT):
        return True
    return bool(os.environ.get("MSA_PASSWORD", ""))


def _read_file_schema(path: Path) -> _FileSchema:
    """Read + parse arrays.json, returning an empty schema when absent/invalid."""
    with suppress(OSError, json.JSONDecodeError):
        return _FileSchema.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return _FileSchema()


def _entry_record(entry: _FileEntry) -> dict[str, object]:
    """Serialize one entry to its on-disk form. Never passwords (unless plaintext fallback)."""
    hosts = entry.host_list()
    record: dict[str, object] = {"name": entry.name}
    # Prefer the compact single-host form when there's exactly one address.
    if len(hosts) == 1:
        record["host"] = hosts[0]
    else:
        record["hosts"] = hosts
    if entry.username:
        record["username"] = entry.username
    if entry.password:  # only present in the keychain-unavailable fallback
        record["password"] = entry.password
    return record


def _write_config_file(
    path: Path,
    defaults_username: str | None,
    entries: list[_FileEntry],
    *,
    defaults_extra: _FileDefaults | None = None,
) -> None:
    """Write the whole arrays.json (defaults + arrays) with mode 0o600."""
    defaults_record: dict[str, object] = {}
    if defaults_username:
        defaults_record["username"] = defaults_username
    if defaults_extra is not None and defaults_extra.password:
        # Plaintext default password only when the keychain is unavailable.
        defaults_record["password"] = defaults_extra.password

    config: dict[str, object] = {}
    if defaults_record:
        config["defaults"] = defaults_record
    config["arrays"] = [_entry_record(e) for e in entries]
    _write_restricted(path, json.dumps(config, indent=2) + "\n")


def _keyring_available() -> bool:
    """True when the ``keyring`` backend can store/retrieve passwords.

    Detects headless Linux servers (no ``libsecret``) where the default
    ``keyring`` backend raises on use. We probe with a no-op read; if it
    raises, we fall back to plaintext storage in arrays.json.
    """
    try:
        keyring.get_password(KEYRING_SERVICE, _AVAILABILITY_ACCOUNT)
        return True
    except Exception:
        return False


def _normalize_host_str(host: str) -> str:
    """Strip scheme + trailing slashes from a hostname string."""
    cleaned = host.strip()
    cleaned = re.sub(r"^https?://", "", cleaned, flags=re.IGNORECASE)
    return cleaned.rstrip("/")


def _write_restricted(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` with mode ``0o600``.

    Always re-applies chmod after write because the ``mode`` argument to
    ``open()`` only affects new files. On Windows this is best-effort and
    typically a no-op against ACLs.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        with suppress(OSError):
            os.close(fd)
        raise
    with suppress(OSError):
        os.chmod(path, 0o600)


__all__ = [
    "DEFAULT_KEYRING_ACCOUNT",
    "KEYRING_SERVICE",
    "ArrayEntry",
    "get_config_path",
    "get_defaults",
    "is_multi_array",
    "list_array_entries",
    "load_array_config",
    "remove_array_entry",
    "resolve_array",
    "save_array_entry",
    "save_defaults",
]
