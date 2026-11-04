"""Performance statistics read tools: controller, volume, and disk counters.

These return dense numeric counters (IOPS, bytes/sec, cache hits, latency), so
the lean view is drop-None only — use ``fields`` to project the handful of
counters you care about, or ``verbose`` for the full set.
"""

from __future__ import annotations

from typing import Any

from node804_mcp_toolkit import FieldsList, Mode, Pagination, Pattern, VerboseFlag

from ..responses.shapers import (
    SHAPE_CONTROLLER_STATISTICS,
    SHAPE_DISK_STATISTICS,
    SHAPE_VOLUME_STATISTICS,
)
from ._common import ArrayParam, ToolContext, fetch_list


def register_statistics_tools(ctx: ToolContext) -> None:
    """Register the performance-statistics read tools against ``ctx.mcp``."""

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_controller_statistics(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
    ) -> dict[str, Any]:
        """Per-controller performance counters: IOPS, throughput, cache utilization."""
        return await fetch_list(
            ctx,
            array,
            ["show", "controller-statistics"],
            preferred_key="controller-statistics",
            shape=SHAPE_CONTROLLER_STATISTICS,
            verbose=verbose,
            fields=fields,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_volume_statistics(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Per-volume performance counters: IOPS, data rate, cache hits.

        ``pattern`` filters by volume name.
        """
        return await fetch_list(
            ctx,
            array,
            ["show", "volume-statistics"],
            preferred_key="volume-statistics",
            shape=SHAPE_VOLUME_STATISTICS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_disk_statistics(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Per-disk performance counters: IOPS, data rate, average latency."""
        return await fetch_list(
            ctx,
            array,
            ["show", "disk-statistics"],
            preferred_key="disk-statistics",
            shape=SHAPE_DISK_STATISTICS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )


__all__ = ["register_statistics_tools"]
