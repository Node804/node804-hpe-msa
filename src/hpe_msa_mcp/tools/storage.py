"""Storage read tools: pools, disk-groups, volumes, snapshots.

The logical-capacity surface — how physical disks roll up into pools and disk
groups, and how volumes and snapshots are carved out of them.
"""

from __future__ import annotations

from typing import Any

from node804_mcp_toolkit import FieldsList, Mode, Pagination, Pattern, VerboseFlag

from ..responses.shapers import (
    SHAPE_DISK_GROUPS,
    SHAPE_POOLS,
    SHAPE_SNAPSHOTS,
    SHAPE_VOLUMES,
)
from ._common import ArrayParam, ToolContext, fetch_list


def register_storage_tools(ctx: ToolContext) -> None:
    """Register the storage read tools against ``ctx.mcp``."""

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_pools(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Virtual storage pools (A/B): total vs available capacity, health."""
        return await fetch_list(
            ctx,
            array,
            ["show", "pools"],
            preferred_key="pools",
            shape=SHAPE_POOLS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_disk_groups(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Disk groups: RAID type, disk count, size/free, pool membership, health."""
        return await fetch_list(
            ctx,
            array,
            ["show", "disk-groups"],
            preferred_key="disk-groups",
            shape=SHAPE_DISK_GROUPS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_volumes(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Volumes: size, allocation, pool, WWN, health.

        ``pattern`` filters by volume name. Arrays with many volumes should use
        pagination or a pattern to bound the response.
        """
        return await fetch_list(
            ctx,
            array,
            ["show", "volumes"],
            preferred_key="volumes",
            shape=SHAPE_VOLUMES,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_snapshots(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Snapshots: parent/source volume, creation time, size, health.

        Requires a snapshot license. On arrays without one this returns an
        empty list (the command succeeds but reports no snapshots).
        """
        return await fetch_list(
            ctx,
            array,
            ["show", "snapshots"],
            preferred_key="snapshots",
            shape=SHAPE_SNAPSHOTS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )


__all__ = ["register_storage_tools"]
