"""Hardware read tools: disks, sensors, fans, power supplies, expander status,
certificate.

The physical-health surface of the array — what you read to answer "is any
component degraded or failed?".
"""

from __future__ import annotations

from typing import Any

from node804_mcp_toolkit import FieldsList, Mode, Pagination, Pattern, VerboseFlag

from ..responses.shapers import (
    SHAPE_CERTIFICATE,
    SHAPE_DISKS,
    SHAPE_EXPANDER_STATUS,
    SHAPE_FANS,
    SHAPE_POWER_SUPPLIES,
    SHAPE_SENSORS,
)
from ._common import ArrayParam, ToolContext, fetch_list


def register_hardware_tools(ctx: ToolContext) -> None:
    """Register the hardware read tools against ``ctx.mcp``."""

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_disks(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Physical disks: location, model, size, type, health, group membership.

        ``pattern`` filters by disk location (e.g. ``0.3`` for enclosure 0 slot
        3). Large arrays can have hundreds of disks — use pagination or a
        pattern to bound the response.
        """
        return await fetch_list(
            ctx,
            array,
            ["show", "disks"],
            preferred_key="drives",
            shape=SHAPE_DISKS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_sensor_status(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Environmental sensors: temperature, voltage, current — value + status."""
        return await fetch_list(
            ctx,
            array,
            ["show", "sensor-status"],
            preferred_key="sensors",
            shape=SHAPE_SENSORS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_fans(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Cooling fans: speed (RPM), status, health, location."""
        return await fetch_list(
            ctx,
            array,
            ["show", "fans"],
            preferred_key="fan",
            shape=SHAPE_FANS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_power_supplies(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Power supply units (PSUs): status, health, voltage/temperature rails."""
        return await fetch_list(
            ctx,
            array,
            ["show", "power-supplies"],
            preferred_key="power-supplies",
            shape=SHAPE_POWER_SUPPLIES,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_expander_status(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """SAS expander status per enclosure/drawer — the disk interconnect fabric."""
        return await fetch_list(
            ctx,
            array,
            ["show", "expander-status"],
            preferred_key="sas-status-controller-a",
            shape=SHAPE_EXPANDER_STATUS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_certificate(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
    ) -> dict[str, Any]:
        """Management TLS certificate status per controller.

        The lean view omits the raw certificate text; set verbose=true to
        include the full PEM/signature blob.
        """
        return await fetch_list(
            ctx,
            array,
            ["show", "certificate"],
            preferred_key="certificate-status",
            shape=SHAPE_CERTIFICATE,
            verbose=verbose,
            fields=fields,
        )


__all__ = ["register_hardware_tools"]
