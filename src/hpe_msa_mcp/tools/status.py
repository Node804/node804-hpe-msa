"""Diagnostic tools: ``server_status`` (local) and ``check_connectivity`` (live).

``server_status`` is always available and makes no network call. It reports the
active RBAC mode, TLS posture, audit destination, and configured array registry
— everything needed to confirm deployment configuration without reading logs.

``check_connectivity`` does a real login + ``show/system`` round-trip against
the target so an operator can confirm credentials and reachability.
"""

from __future__ import annotations

from typing import Any

from node804_mcp_toolkit import AuditSink, Mode, describe_tls

from ..config.arrays import get_defaults, is_multi_array, list_array_entries
from ..responses.shapers import extract_objects
from ._common import ArrayParam, ToolContext, request_payload


def register_status_tools(ctx: ToolContext, *, audit_sink: AuditSink) -> None:
    """Register the diagnostic tools against ``ctx.mcp``."""

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def server_status() -> dict[str, Any]:
        """Report the hpe-msa-mcp server's current posture.

        Returns the active RBAC mode, tool counts, TLS verification state, audit
        destination, and the configured array registry. Makes no network call —
        use ``check_connectivity`` to test reachability of an array.
        """
        summary = ctx.gate.summary()
        return {
            "mode": summary["mode"],
            "tools_total": summary["tools_total"],
            "tools_enabled": summary["tools_enabled"],
            "tools_blocked": summary["tools_blocked"],
            "blocked_tools": summary["blocked_tools"],
            "capabilities": {
                "read": True,
                # This server intentionally registers nothing above READ.
                "write": ctx.gate.allows(Mode.STANDARD),
            },
            "tls": describe_tls(ctx.tls),
            "audit": audit_sink.describe(),
            "arrays": {
                "configured": list_array_entries(),
                "multi_array": is_multi_array(),
                "defaults": get_defaults(),
            },
        }

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def check_connectivity(array: ArrayParam = None) -> dict[str, Any]:
        """Verify credentials and reachability with a live ``show/system`` round-trip.

        Logs in (if needed) and reads system info. Returns ``{"reachable": true,
        ...}`` with the array's name/product/health on success, or ``{"reachable":
        false, "error": ...}`` on any auth/transport/command failure.
        """
        payload = await request_payload(ctx, array, ["show", "system"])
        if "error" in payload:
            return {"reachable": False, "error": payload["error"]}
        objects = extract_objects(payload, "system")
        system = objects[0] if objects else {}
        return {
            "reachable": True,
            "system-name": system.get("system-name"),
            "product-id": system.get("product-id"),
            "health": system.get("health"),
            "midplane-serial-number": system.get("midplane-serial-number"),
        }


__all__ = ["register_status_tools"]
