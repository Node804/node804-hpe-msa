"""System-level read tools: system info, versions, controllers, enclosures,
FRUs, license, NTP, network.

Thin wrappers over the shaping pipeline: resolve the array, run a ``show``
command, extract the object array, and apply the per-tool lean whitelist.
"""

from __future__ import annotations

from typing import Any

from node804_mcp_toolkit import FieldsList, Mode, Pagination, Pattern, VerboseFlag

from ..responses.shapers import (
    SHAPE_CONTROLLERS,
    SHAPE_ENCLOSURES,
    SHAPE_FRUS,
    SHAPE_LICENSE,
    SHAPE_NETWORK,
    SHAPE_NTP_STATUS,
    SHAPE_SYSTEM,
    SHAPE_VERSIONS,
)
from ._common import ArrayParam, ToolContext, fetch_list, fetch_single


def register_system_tools(ctx: ToolContext) -> None:
    """Register the system-level read tools against ``ctx.mcp``."""

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_system(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
    ) -> dict[str, Any]:
        """Overall system info: name, product, serial, health, contact/location.

        Default response includes the fields most operators care about; set
        verbose=true for vendor/brand/scsi and other platform details.
        """
        return await fetch_single(
            ctx,
            array,
            ["show", "system"],
            preferred_key="system",
            shape=SHAPE_SYSTEM,
            verbose=verbose,
            fields=fields,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_versions(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
    ) -> dict[str, Any]:
        """Firmware and hardware version bundle for each controller."""
        return await fetch_list(
            ctx,
            array,
            ["show", "versions"],
            preferred_key="versions",
            shape=SHAPE_VERSIONS,
            verbose=verbose,
            fields=fields,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_controllers(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Controller modules (A/B): status, health, firmware, IP, redundancy.

        MSA arrays have two controllers; check ``redundancy-status`` to confirm
        the pair is redundant. Filter by controller id with ``pattern``.
        """
        return await fetch_list(
            ctx,
            array,
            ["show", "controllers"],
            preferred_key="controllers",
            shape=SHAPE_CONTROLLERS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_enclosures(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Enclosures (head unit + expansion shelves): model, status, health."""
        return await fetch_list(
            ctx,
            array,
            ["show", "enclosures"],
            preferred_key="enclosures",
            shape=SHAPE_ENCLOSURES,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_frus(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Field-replaceable units (FRUs): part/serial numbers and status.

        The inventory an engineer needs when ordering a replacement part.
        """
        return await fetch_list(
            ctx,
            array,
            ["show", "frus"],
            preferred_key="enclosure-fru",
            shape=SHAPE_FRUS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_license(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
    ) -> dict[str, Any]:
        """Installed license features and their entitlement status."""
        return await fetch_list(
            ctx,
            array,
            ["show", "license"],
            preferred_key="license",
            shape=SHAPE_LICENSE,
            verbose=verbose,
            fields=fields,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_ntp_status(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
    ) -> dict[str, Any]:
        """NTP time-sync status: enabled state, server, last contact time."""
        return await fetch_single(
            ctx,
            array,
            ["show", "ntp-status"],
            preferred_key="ntp-status",
            shape=SHAPE_NTP_STATUS,
            verbose=verbose,
            fields=fields,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_network(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
    ) -> dict[str, Any]:
        """Management-port network config per controller: IP, gateway, MAC, link."""
        return await fetch_list(
            ctx,
            array,
            ["show", "network-parameters"],
            preferred_key="network-parameters",
            shape=SHAPE_NETWORK,
            verbose=verbose,
            fields=fields,
        )


__all__ = ["register_system_tools"]
