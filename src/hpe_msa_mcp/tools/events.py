"""Event and alert read tools.

``get_events`` reads the controller event log (bounded by a count, optionally
filtered by severity); ``get_alerts`` reads the Gen6 active-alerts list.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from node804_mcp_toolkit import FieldsList, Mode, Pagination, Pattern, VerboseFlag
from pydantic import Field

from ..responses.shapers import SHAPE_ALERTS, SHAPE_EVENTS
from ._common import ArrayParam, ToolContext, fetch_list

EventCount = Annotated[
    int,
    Field(
        default=100,
        ge=1,
        le=400,
        description=(
            "How many of the most recent events to read (MSA 'last N'). Default "
            "100; max 400. The event log is large — keep this tight and use "
            "severity to focus."
        ),
    ),
]

EventSeverity = Annotated[
    Literal["informational", "warning", "error", "critical", "resolved"] | None,
    Field(
        default=None,
        description=(
            "Optional severity filter applied by the array. None (default) "
            "returns all severities. 'error'/'critical' surface the events that "
            "usually matter for a health check."
        ),
    ),
]


def register_events_tools(ctx: ToolContext) -> None:
    """Register the event/alert read tools against ``ctx.mcp``."""

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_events(
        array: ArrayParam = None,
        count: EventCount = 100,
        severity: EventSeverity = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Recent controller events: timestamp, code, severity, message, recommended action.

        Reads the ``count`` most recent events (optionally filtered by
        ``severity`` at the array). ``pattern`` further filters client-side by
        event id. For a fast "anything wrong?" check, use severity='error'.
        """
        segments = ["show", "events"]
        if severity is not None:
            segments.append(severity)
        segments += ["last", str(count)]
        return await fetch_list(
            ctx,
            array,
            segments,
            preferred_key="events",
            shape=SHAPE_EVENTS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )

    @ctx.gate.tool(ctx.mcp, required=Mode.READ)
    async def get_alerts(
        array: ArrayParam = None,
        verbose: VerboseFlag = False,
        fields: FieldsList = None,
        pattern: Pattern = None,
        pagination: Pagination = Pagination(),
    ) -> dict[str, Any]:
        """Active alerts (Gen6): severity, condition, detail, acknowledgement state.

        Alerts are the current outstanding conditions the array wants attention
        on — a shorter, higher-signal list than the raw event log. On firmware
        without the alerts command this returns an empty list.
        """
        return await fetch_list(
            ctx,
            array,
            ["show", "alerts"],
            preferred_key="alert",
            shape=SHAPE_ALERTS,
            verbose=verbose,
            fields=fields,
            pattern=pattern,
            pagination=pagination,
        )


__all__ = ["register_events_tools"]
