"""Shared helpers used by every read tool.

The common pattern for an MSA read tool is:

1. Resolve the :class:`MSAClient` for the requested array (or return an error dict).
2. Run a ``show`` command over REST, getting the response envelope.
3. Check the trailing ``status`` — an auth/transport/command error must surface
   as an error, never as an empty inventory.
4. Extract the object array and shape it (:func:`fetch_list`) or take the first
   object and shape it (:func:`fetch_single`).

:func:`fetch_list` and :func:`fetch_single` package that whole flow so tool
modules stay one-liners over the array. A :class:`ToolContext` bundles the
cross-cutting state (FastMCP instance, RBAC gate, TLS config, audit sink) the
server hands to every ``register_*_tools`` function.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from node804_mcp_toolkit import ModeGate, Pagination, TlsConfig
from pydantic import Field

from ..client import (
    MSAClient,
    ResolveError,
    _extract_status,
    _status_is_error,
    get_client,
)
from ..responses.shapers import LeanShape, apply_shape, extract_objects, shape_single

ArrayParam = Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "Name of the configured MSA array to target. Optional when a single "
            "array is configured (or the MSA_HOST env fallback is used); REQUIRED "
            "when multiple arrays are configured. Use list_arrays to see names."
        ),
    ),
]
"""Standard per-tool array selector. Mirrors the ``firewall`` param in panos-mcp."""


@dataclass(frozen=True)
class ToolContext:
    """Cross-cutting state passed to every ``register_*_tools`` function.

    The server builds one at startup; tool modules use it to decorate handlers
    (``ctx.gate.tool(ctx.mcp, ...)``) and resolve clients (``ctx.resolve(array)``).
    """

    mcp: FastMCP
    gate: ModeGate
    tls: TlsConfig
    audit_sink: Any = None

    def resolve(self, array: str | None) -> MSAClient | dict[str, Any]:
        """Resolve an array name to a logged-in client or an error dict.

        Returns either the live :class:`MSAClient` or ``{"error": "..."}``.
        Tools check ``isinstance(result, dict)`` to short-circuit::

            client = ctx.resolve(array)
            if isinstance(client, dict):
                return client
        """
        result = get_client(array, tls=self.tls)
        if isinstance(result, ResolveError):
            return {"error": result.message}
        return result


async def run_blocking[T](callable_fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a synchronous MSAClient call in a thread executor.

    ``MSAClient.request`` is synchronous httpx; running it inline in a coroutine
    would stall FastMCP's event loop for the request duration. Routing it
    through the default thread executor keeps the loop responsive so other tool
    calls can proceed.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: callable_fn(*args, **kwargs))


def _status_error_dict(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return an error dict when the MSA command status reports failure, else None.

    The status ``response`` text is the array's own human-readable message
    (e.g. "Command not recognized" or a parameter error). Surfacing it — rather
    than an empty list — is what keeps a failed command from reading as "nothing
    configured".
    """
    status = _extract_status(payload)
    if _status_is_error(status):
        message = str(status.get("response") or "MSA command failed")
        return {"error": message}
    return None


async def request_payload(
    ctx: ToolContext, array: str | None, segments: list[str]
) -> dict[str, Any]:
    """Resolve + run a command, returning the raw envelope or an ``{"error": ...}``.

    Aggregation tools that need to inspect several object arrays from one call
    use this directly. List/single tools go through :func:`fetch_list` /
    :func:`fetch_single`.
    """
    client = ctx.resolve(array)
    if isinstance(client, dict):
        return client
    result = await run_blocking(client.request, segments)
    if isinstance(result, ResolveError):
        return {"error": result.message}
    error = _status_error_dict(result)
    if error is not None:
        return error
    return result


async def fetch_list(
    ctx: ToolContext,
    array: str | None,
    segments: list[str],
    *,
    preferred_key: str | None,
    shape: LeanShape,
    verbose: bool = False,
    fields: list[str] | None = None,
    pagination: Pagination | None = None,
    pattern: str | None = None,
) -> dict[str, Any]:
    """Run a ``show`` command and return a shaped ``{total, count, items}`` list.

    On any resolve/transport/command error returns ``{"error": ...}`` instead —
    never a misleading empty list.
    """
    payload = await request_payload(ctx, array, segments)
    if "error" in payload:
        return payload
    objects = extract_objects(payload, preferred_key)
    return apply_shape(
        objects,
        shape=shape,
        verbose=verbose,
        fields=fields,
        pagination=pagination,
        pattern=pattern,
    )


async def fetch_single(
    ctx: ToolContext,
    array: str | None,
    segments: list[str],
    *,
    preferred_key: str | None,
    shape: LeanShape,
    verbose: bool = False,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Run a ``show`` command and return the first object, shaped.

    For commands that conceptually return one record (system info, NTP status).
    Returns ``{"error": ...}`` on failure and ``{}`` when the command succeeded
    but carried no object.
    """
    payload = await request_payload(ctx, array, segments)
    if "error" in payload:
        return payload
    objects = extract_objects(payload, preferred_key)
    first = objects[0] if objects else None
    return shape_single(first, shape=shape, verbose=verbose, fields=fields)


__all__ = [
    "ArrayParam",
    "ToolContext",
    "fetch_list",
    "fetch_single",
    "request_payload",
    "run_blocking",
]
