"""Utility tools: generic show escape hatch + array registry management.

- ``run_show_command`` (Mode.READ) — run any read-only ``show`` command not
  covered by a dedicated tool. Hard-restricted to the ``show`` verb: the verb
  is fixed by this code, never taken from the caller, so it can't be used to
  mutate the array.
- ``list_arrays`` (Mode.READ) — list configured array targets (never passwords).
- ``add_array`` (Mode.READ) — register a new array in the local config +
  keychain. This writes *local MCP config*, not the array, so it's within the
  read-only posture toward the storage itself.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from node804_mcp_toolkit import FieldsList, Mode, Pagination, Pattern, VerboseFlag
from pydantic import Field

from ..config.arrays import (
    ArrayEntry,
    get_defaults,
    is_multi_array,
    list_array_entries,
    resolve_array,
    save_array_entry,
    save_defaults,
)
from ..responses.shapers import SHAPE_GENERIC
from ._common import ArrayParam, ToolContext, fetch_list

# A show sub-command is a single lowercase token: 'disks', 'pools',
# 'disk-groups'. No slashes (the verb 'show' is fixed in code, so a token can't
# smuggle a second path segment) and no whitespace.
_COMMAND_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

# Each positional arg is a safe path token: severities ('error'), counts
# ('100'), keywords ('last', 'detail'), locations ('0.1'). Deliberately narrow
# — names with spaces or special characters can't ride through a URL path
# segment anyway, and a narrow charset keeps this escape hatch injection-proof.
_ARG_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

ShowCommand = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        description=(
            "The ``show`` sub-command to run, WITHOUT the leading 'show' "
            "(e.g. 'disk-groups', 'pools', 'volume-names'). Lowercase letters, "
            "digits, and hyphens only. The 'show' verb is enforced by the "
            "server — this tool cannot run any mutating command."
        ),
    ),
]

ShowArgs = Annotated[
    list[str] | None,
    Field(
        default=None,
        description=(
            "Optional positional arguments appended after the command, as MSA "
            "CLI path tokens (e.g. ['last', '50'] for 'show events last 50', or "
            "a disk-group name). Each token is restricted to a safe charset; "
            "values with spaces are not supported through this escape hatch."
        ),
    ),
]


def register_utility_tools(ctx: ToolContext) -> None:
    """Register the utility tools against ``ctx.mcp``."""

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def run_show_command(
        command: ShowCommand,
        args: ShowArgs = None,
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Run a read-only MSA ``show`` command not covered by a dedicated tool.

        The server always prepends the ``show`` verb and validates the command
        and every argument against a safe token charset, so this can only ever
        issue a ``show`` (read-only) request. The response is shaped with the
        generic lean pipeline (drop-None + pattern/pagination); pass ``fields``
        to project specific keys or ``verbose`` for the full objects.

        Examples: command='ports', or command='events' args=['warning','last','20'].
        """
        if not _COMMAND_RE.match(command):
            return {
                "error": (
                    f"Invalid show command '{command}'. Use a single lowercase "
                    "token like 'disks' or 'disk-groups' (no 'show' prefix, no slashes)."
                )
            }
        clean_args = args or []
        for tok in clean_args:
            if not _ARG_RE.match(tok):
                return {
                    "error": (
                        f"Invalid argument token '{tok}'. Arguments must be simple "
                        "path tokens (letters, digits, dot, colon, hyphen, underscore)."
                    )
                }
        segments = ["show", command, *clean_args]
        # No preferred key — let extract_objects auto-detect the object array.
        return await fetch_list(
            ctx,
            array,
            segments,
            preferred_key=None,
            shape=SHAPE_GENERIC,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def list_arrays() -> dict[str, Any]:
        """List configured MSA array targets and shared defaults (never passwords).

        Each array shows its ``hosts`` (failover-ordered management addresses)
        and the ``username`` it resolves to. ``defaults`` shows the shared
        username and whether a shared default password is set.
        """
        entries = list_array_entries()
        return {
            "count": len(entries),
            "multi_array": is_multi_array(),
            "defaults": get_defaults(),
            "arrays": entries,
        }

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def set_default_credentials(
        username: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Set the shared default username and/or password used by all arrays.

        For environments with the same monitoring account on every array: set
        the credentials once here, then register arrays with just a name + host
        (``add_array`` without username/password). Any array can still override.

        Writes *local MCP configuration*: the username goes to the ``defaults``
        block in arrays.json; the password goes to the OS keychain default
        account (plaintext in arrays.json, mode 0600, only as a headless
        fallback). The password is never echoed back. Either argument may be
        omitted to change only the other.
        """
        if username is None and password is None:
            return {"success": False, "error": "Provide a username, a password, or both."}
        try:
            save_defaults(username=username, password=password)
        except Exception as exc:
            from .._redact import redact_exception

            return {"success": False, "error": redact_exception(exc)}
        return {"success": True, "defaults": get_defaults()}

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def add_array(
        name: str,
        host: str | None = None,
        hosts: list[str] | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Register a new MSA array in the local config (+ OS keychain).

        One entry per array (not per controller). Provide a single ``host`` (the
        management VIP is ideal), or ``hosts`` — a failover-ordered list, e.g.
        both controller management IPs — tried in order until one answers.

        ``username`` and ``password`` are OPTIONAL: omit them to inherit the
        shared defaults set via ``set_default_credentials`` (the common case
        when every array uses the same monitoring account). When supplied, the
        username is stored in arrays.json and the password in the OS keychain
        (plaintext in arrays.json, mode 0600, only as a headless fallback); the
        password is never echoed back.

        This writes *local MCP configuration* only — it does NOT modify the
        storage array, so it stays within the read-only posture toward hardware.
        """
        host_list: list[str] = []
        if host:
            host_list.append(host)
        if hosts:
            host_list.extend(hosts)
        if not host_list:
            return {"success": False, "error": "Provide 'host' or 'hosts' (at least one address)."}

        defaults = get_defaults()
        default_username = defaults.get("username")
        effective_username = username or (
            default_username if isinstance(default_username, str) else None
        )
        if not effective_username:
            return {
                "success": False,
                "error": (
                    "No username given and no shared default is set. Pass 'username' or call "
                    "set_default_credentials first."
                ),
            }
        # A password must be resolvable: either supplied now, or a shared default exists.
        if not password and not defaults.get("password_set"):
            return {
                "success": False,
                "error": (
                    "No password given and no shared default is set. Pass 'password' or call "
                    "set_default_credentials first."
                ),
            }

        try:
            # password=None → the entry inherits the shared default at load time.
            entry = ArrayEntry(
                name=name,
                hosts=host_list,
                username=effective_username,
                password=password or None,
            )
            save_array_entry(entry)
        except Exception as exc:
            from .._redact import redact_exception

            return {"success": False, "error": f"Invalid array entry: {redact_exception(exc)}"}

        resolved = resolve_array(name)
        return {
            "success": True,
            "name": name,
            "hosts": list(resolved.hosts) if resolved else host_list,
            "username": effective_username,
            "inherits_default_password": not bool(password),
            "multi_array": is_multi_array(),
        }


__all__ = ["register_utility_tools"]
