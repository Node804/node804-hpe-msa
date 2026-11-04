"""FastMCP server entrypoint for hpe-msa-mcp.

A read-only tool surface over the HPE MSA REST API plus the always-on
diagnostic tools. Every read tool returns default-lean responses (verbose=true
to opt out) with pagination / pattern filtering.

Environment variables:

| Var                   | Purpose                                              |
|-----------------------|------------------------------------------------------|
| ``MSA_MODE``          | RBAC mode. Only ``read`` registers tools. Default: read. |
| ``MSA_AUDIT_LOG``     | Path to JSON-lines audit log. Unset = audit disabled. |
| ``MSA_TLS_VERIFY``    | true (default) / false. MSA certs are often self-signed. |
| ``MSA_TLS_CA``        | Optional path to a custom CA PEM bundle.             |
| ``MSA_ARRAYS_CONFIG`` | Override path for arrays.json.                       |
| ``MSA_HOST``          | Single-array fallback hostname.                      |
| ``MSA_USERNAME``      | Single-array fallback username.                      |
| ``MSA_PASSWORD``      | Single-array fallback password.                      |
| ``MSA_API_TIMEOUT``   | Per-request timeout in seconds. Default: 30.         |
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP
from node804_mcp_toolkit import Mode, ModeGate, describe_tls, open_sink, resolve_tls_config

from . import __version__
from .config.arrays import is_multi_array, load_array_config
from .tools._common import ToolContext
from .tools.aggregations import register_aggregation_tools
from .tools.events import register_events_tools
from .tools.hardware import register_hardware_tools
from .tools.hosts import register_hosts_tools
from .tools.statistics import register_statistics_tools
from .tools.status import register_status_tools
from .tools.storage import register_storage_tools
from .tools.system import register_system_tools
from .tools.utility import register_utility_tools

# --- Toolkit-driven setup ---------------------------------------------------

audit_sink = open_sink(env_var="MSA_AUDIT_LOG")
"""Where audit events go. No-op when MSA_AUDIT_LOG is unset."""

tls_config = resolve_tls_config(dict(os.environ), prefix="MSA")
"""TLS verification config. Verify-by-default."""

gate = ModeGate.from_env(
    env_var="MSA_MODE",
    default=Mode.READ,
    audit_sink=audit_sink,
)
"""RBAC gate. Default-deny posture: MSA_MODE unset = read-only access.

This server is read-only by construction — no tool above ``Mode.READ`` is
registered, so raising the mode changes nothing. The gate + env var exist for
consistency with the suite and to leave a seam for a future write surface."""

# Always-on operating context injected into every session. Kept tight; states
# the invariants that keep an MSA read honest — two controllers, health lives
# in health/health-reason, and a failed command must never read as empty.
SERVER_INSTRUCTIONS = """\
HPE MSA storage tool semantics — read before interpreting results.

Read-only server: every tool is a 'show' over the MSA REST API. There are no
write/provisioning tools; MSA_MODE never registers any.

An MSA array has TWO controllers (A and B). Component health lives in the
'health' field (OK / Degraded / Fault / N/A) with a 'health-reason' string —
prefer these over inferring health from status codes. For "is this array
healthy?", call get_health_summary FIRST; it rolls up system health, controller
redundancy, degraded/failed disks, pool capacity, and active alerts in one call,
far cheaper than dumping full inventories.

A tool that returns {"error": ...} hit an auth/transport/command failure —
never read that as "nothing configured". An empty items list from a successful
call genuinely means none exist. Check for the error key first.

Multi-array: when several arrays are configured, every tool REQUIRES the
'array' parameter (use list_arrays to see names). With one array (or the
MSA_HOST env fallback) it's optional.

Token discipline: prefer get_health_summary and pattern=/fields=/pagination on
list tools before pulling full inventories (disks and volumes can number in the
hundreds).
"""

mcp = FastMCP("hpe-msa-mcp", instructions=SERVER_INSTRUCTIONS)

ctx = ToolContext(mcp=mcp, gate=gate, tls=tls_config, audit_sink=audit_sink)
"""Shared context every register_*_tools function consumes."""

# Diagnostic tools (always available under READ).
register_status_tools(ctx, audit_sink=audit_sink)

# Read tools.
register_system_tools(ctx)
register_hardware_tools(ctx)
register_storage_tools(ctx)
register_hosts_tools(ctx)
register_events_tools(ctx)
register_statistics_tools(ctx)
register_aggregation_tools(ctx)
register_utility_tools(ctx)


def main() -> None:
    """Entry point for the ``node804-hpe-msa`` console script."""
    load_array_config()
    print(f"hpe-msa-mcp {__version__} starting", file=sys.stderr)
    print(f"MSA TLS:   {describe_tls(tls_config)}", file=sys.stderr)
    print(f"MSA RBAC:  {gate.describe()}", file=sys.stderr)
    print(f"MSA audit: {audit_sink.describe()}", file=sys.stderr)
    multi = "multiple arrays" if is_multi_array() else "single array / env fallback"
    print(f"MSA arrays: {multi}", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
