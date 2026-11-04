"""Host-connectivity read tools: host groups, hosts, initiators, maps, ports.

The SAN-attach surface — how initiators are grouped into hosts and host groups,
which volumes are mapped to them, and the state of the array's target ports.
"""

from __future__ import annotations

from typing import Any

from node804_mcp_toolkit import FieldsList, Mode, Pagination, Pattern, VerboseFlag

from ..responses.shapers import (
    SHAPE_HOST_GROUPS,
    SHAPE_HOSTS,
    SHAPE_INITIATORS,
    SHAPE_MAPS,
    SHAPE_PORTS,
)
from ._common import ArrayParam, ToolContext, fetch_list


def register_hosts_tools(ctx: ToolContext) -> None:
    """Register the host-connectivity read tools against ``ctx.mcp``."""

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_host_groups(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Host groups: named collections of hosts used for shared volume mapping."""
        return await fetch_list(
            ctx,
            array,
            ["show", "host-groups"],
            preferred_key="host-group",
            shape=SHAPE_HOST_GROUPS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_hosts(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Hosts: named collections of initiators (one server's HBA ports)."""
        return await fetch_list(
            ctx,
            array,
            ["show", "hosts"],
            preferred_key="host",
            shape=SHAPE_HOSTS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_initiators(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Initiators (FC WWNs / iSCSI IQNs / SAS addresses): nickname, host, mapped state.

        ``pattern`` filters by initiator nickname.
        """
        return await fetch_list(
            ctx,
            array,
            ["show", "initiators"],
            preferred_key="initiator",
            shape=SHAPE_INITIATORS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_maps(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Volume-to-host LUN mappings: which host/initiator sees which volume, at what LUN.

        ``pattern`` filters by volume name.
        """
        return await fetch_list(
            ctx,
            array,
            ["show", "maps"],
            preferred_key="volume-view",
            shape=SHAPE_MAPS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_ports(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Array target host ports (FC/iSCSI/SAS): type, status, health, speed."""
        return await fetch_list(
            ctx,
            array,
            ["show", "ports"],
            preferred_key="port",
            shape=SHAPE_PORTS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )


__all__ = ["register_hosts_tools"]
