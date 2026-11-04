"""Utility tools: generic show escape hatch + array registry inspection.

- ``run_show_command`` (Mode.READ) — run any read-only ``show`` command not
  covered by a dedicated tool. Hard-restricted to the ``show`` verb: the verb
  is fixed by this code, never taken from the caller, so it can't be used to
  mutate the array.
- ``list_arrays`` (Mode.READ) — list configured array targets (never passwords).

Registering or editing arrays and shared credentials is deliberately NOT exposed
as an MCP tool. That is local config + keychain mutation driven from a prompt,
and a prompt-injection could register an attacker-controlled host inheriting the
shared monitoring credential — a credential-exfil path on the next login. Those
operations live only in the ``node804-hpe-msa-mcp-setup`` CLI wizard, which the
user runs themselves. The underlying ``save_array_entry`` / ``save_defaults`` /
``remove_array_entry`` helpers remain in ``config.arrays`` for the wizard.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from node804_mcp_toolkit import FieldsList, Mode, Pagination, Pattern, VerboseFlag
from pydantic import Field

from ..config.arrays import (
    get_defaults,
    is_multi_array,
    list_array_entries,
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


__all__ = ["register_utility_tools"]
