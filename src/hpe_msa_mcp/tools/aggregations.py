"""Aggregation tools — purpose-built rollups that replace dump-and-grep.

``get_health_summary`` answers "is this array healthy?" in a single tool call
by rolling up overall system health, controller redundancy, degraded/failed
disks, pool capacity, and active alerts. Returning that focused answer costs a
fraction of the tokens of fetching every inventory and letting the model
compute it.

Read-only, like everything else in this server.
"""

from __future__ import annotations

from typing import Any

from node804_mcp_toolkit import Mode

from ..responses.shapers import extract_objects
from ._common import ArrayParam, ToolContext, request_payload


def _is_healthy(value: Any) -> bool:
    """True when an MSA ``health`` value reads as OK.

    MSA reports ``health`` as a string ("OK", "Degraded", "Fault", "N/A",
    "Unknown"). Anything other than OK/N/A is treated as unhealthy — N/A means
    the component has no health rollup (e.g. an empty slot), not a fault.
    """
    text = str(value).strip().lower()
    return text in ("ok", "n/a", "")


def _summarize_component(objects: list[dict[str, Any]], *, id_field: str) -> dict[str, Any]:
    """Roll up a component list into total / unhealthy count / unhealthy detail."""
    unhealthy = [
        {
            "id": obj.get(id_field),
            "health": obj.get("health"),
            "health-reason": obj.get("health-reason"),
        }
        for obj in objects
        if not _is_healthy(obj.get("health"))
    ]
    return {"total": len(objects), "unhealthy": len(unhealthy), "unhealthy_detail": unhealthy}


async def _component_objects(
    ctx: ToolContext, array: str | None, segments: list[str], preferred_key: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch one component list; return (objects, error). error is None on success."""
    payload = await request_payload(ctx, array, segments)
    if "error" in payload:
        return [], str(payload["error"])
    return extract_objects(payload, preferred_key), None


def register_aggregation_tools(ctx: ToolContext) -> None:
    """Register the aggregation tools against ``ctx.mcp``."""

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_health_summary(array: ArrayParam = None) -> dict[str, Any]:
        """One-call health rollup: system, controllers, disks, pools, alerts.

        Answers "is this array healthy?" cheaply. Returns overall system health,
        controller redundancy + per-controller health, a disk summary (total and
        the degraded/failed ones), pool capacity, and the active-alert count by
        severity. Prefer this before dumping full inventories.

        If a section can't be read, its error is reported under that section
        rather than failing the whole summary — except system info, whose
        failure means the array is unreachable and short-circuits the call.
        """
        # System health first — its failure means unreachable/unauthenticated.
        system_payload = await request_payload(ctx, array, ["show", "system"])
        if "error" in system_payload:
            return {"reachable": False, "error": system_payload["error"]}
        system_objs = extract_objects(system_payload, "system")
        system = system_objs[0] if system_objs else {}

        summary: dict[str, Any] = {
            "reachable": True,
            "system": {
                "system-name": system.get("system-name"),
                "product-id": system.get("product-id"),
                "health": system.get("health"),
                "health-reason": system.get("health-reason"),
            },
        }

        # Controllers — redundancy + per-controller health.
        controllers, cerr = await _component_objects(
            ctx, array, ["show", "controllers"], "controllers"
        )
        if cerr is not None:
            summary["controllers"] = {"error": cerr}
        else:
            redundancy = None
            for c in controllers:
                redundancy = c.get("redundancy-status") or redundancy
            summary["controllers"] = {
                "redundancy-status": redundancy,
                **_summarize_component(controllers, id_field="controller-id"),
            }

        # Disks — the most common source of a degraded array.
        disks, derr = await _component_objects(ctx, array, ["show", "disks"], "drives")
        summary["disks"] = (
            {"error": derr}
            if derr is not None
            else _summarize_component(disks, id_field="location")
        )

        # Pools — capacity headline.
        pools, perr = await _component_objects(ctx, array, ["show", "pools"], "pools")
        if perr is not None:
            summary["pools"] = {"error": perr}
        else:
            summary["pools"] = {
                "total": len(pools),
                "detail": [
                    {
                        "name": p.get("name"),
                        "total-size": p.get("total-size"),
                        "total-avail": p.get("total-avail"),
                        "health": p.get("health"),
                    }
                    for p in pools
                ],
            }

        # Active alerts (Gen6). Absent on older firmware → treated as none.
        alerts, aerr = await _component_objects(ctx, array, ["show", "alerts"], "alert")
        if aerr is not None:
            summary["alerts"] = {"error": aerr}
        else:
            by_severity: dict[str, int] = {}
            for a in alerts:
                sev = str(a.get("severity", "unknown")).lower()
                by_severity[sev] = by_severity.get(sev, 0) + 1
            summary["alerts"] = {"active": len(alerts), "by_severity": by_severity}

        # Top-line verdict: healthy only when nothing flagged.
        healthy = _is_healthy(system.get("health"))
        if isinstance(summary["disks"], dict) and summary["disks"].get("unhealthy"):
            healthy = False
        if isinstance(summary["controllers"], dict) and summary["controllers"].get("unhealthy"):
            healthy = False
        summary["healthy"] = healthy
        return summary


__all__ = ["register_aggregation_tools"]
