"""Response shaping + object extraction for token-efficient MSA responses.

This module holds three things that together turn a raw MSA REST envelope into
a lean, model-friendly response:

1. **Object extraction** (:func:`extract_objects`) — pull the object array out
   of the MSA envelope. MSA keys the array by object type, and the key varies
   across firmware, so we try a preferred key then fall back to "first
   list-valued key that isn't ``status``". Never assume a key exists.

2. **The shaping pipeline** (:class:`LeanShape`, :func:`apply_shape`,
   :func:`shape_single`) — filter by name pattern, whitelist fields, drop
   ``None`` values, and paginate. Returns ``{total, count, items}`` for lists.

3. **Per-tool whitelists** (the ``SHAPE_*`` constants) — one source of truth
   for which fields each read tool exposes by default. Anyone auditing token
   efficiency can read exactly what's sent by default here.

The pipeline is pure — no I/O — so it's trivial to unit test.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from node804_mcp_toolkit import Pagination, filter_by_pattern, paginate

# --- Object extraction ------------------------------------------------------

# Preferred JSON key for each ``show`` command's object array. Keys are
# inconsistent across firmware, so these are a *hint*: extract_objects tries
# the preferred key first, then auto-detects. Never assume a key is present.
OBJECT_KEYS: dict[str, str] = {
    "system": "system",
    "controllers": "controllers",
    "disks": "drives",
    "disk-groups": "disk-groups",
    "pools": "pools",
    "volumes": "volumes",
    "snapshots": "snapshots",
    "enclosures": "enclosures",
    "frus": "enclosure-fru",
    "ports": "port",
    "host-groups": "host-group",
    "hosts": "host",
    "initiators": "initiator",
    "maps": "volume-view",
    "sensor-status": "sensors",
    "fans": "fan",
    "power-supplies": "power-supplies",
    "expander-status": "sas-status-controller-a",
    "certificate": "certificate-status",
    "events": "events",
    "alerts": "alert",
    "versions": "versions",
    "license": "license",
    "ntp-status": "ntp-status",
    "network-parameters": "network-parameters",
    "controller-statistics": "controller-statistics",
    "volume-statistics": "volume-statistics",
    "disk-statistics": "disk-statistics",
}

# Envelope keys that are never object arrays.
_ENVELOPE_KEYS = frozenset({"status"})


def extract_objects(payload: Mapping[str, Any], preferred_key: str | None) -> list[dict[str, Any]]:
    """Return the object array from an MSA response envelope.

    Tries ``preferred_key`` first; if that key is absent or not a list, falls
    back to the first list-valued key other than ``status``. Returns an empty
    list only when the envelope genuinely carries no object array — callers
    MUST check command status separately (via the client / ``_common.fetch``)
    so an auth or transport error is never mistaken for an empty inventory.
    """
    if preferred_key is not None:
        value = payload.get(preferred_key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    for key, value in payload.items():
        if key in _ENVELOPE_KEYS:
            continue
        if isinstance(value, list) and all(isinstance(i, dict) for i in value):
            return [item for item in value if isinstance(item, dict)]
    return []


# --- Shaping pipeline -------------------------------------------------------


@dataclass(frozen=True)
class LeanShape:
    """Per-tool token-efficiency configuration.

    Keep one per read tool in the ``SHAPE_*`` constants below, so every field
    whitelist lives in one auditable place.
    """

    default_fields: tuple[str, ...] = ()
    """Fields included when ``verbose=False`` and ``fields=None``.

    An empty tuple means "include all fields" (lean shaping degenerates to
    drop-None only) — useful for terse responses (versions, statistics) where a
    per-field whitelist adds no value.
    """

    drop_when_none: bool = True
    """Drop ``None`` values from each item. MSA objects expose many optional
    fields; ``None`` carries no signal but real token cost."""

    name_field: str = "name"
    """Field the pattern filter matches against. Overridden per object type —
    MSA names its objects inconsistently (``volume-name``, ``durable-id``, …)."""

    extra_strip_keys: tuple[str, ...] = field(default_factory=tuple)
    """Keys to drop unconditionally, recursively (e.g. ``object-name`` boilerplate
    or bulky embedded blobs)."""


def _project_fields(
    item: Mapping[str, Any],
    fields: Iterable[str] | None,
    default_fields: tuple[str, ...],
    verbose: bool,
) -> dict[str, Any]:
    """Apply field whitelisting per the shape rules."""
    if fields is not None:
        keep = set(fields)
        return {k: v for k, v in item.items() if k in keep}
    if verbose or not default_fields:
        return dict(item)
    keep = set(default_fields)
    return {k: v for k, v in item.items() if k in keep}


def _drop_nones(item: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if v is not None}


def _strip_keys_recursive(value: Any, drop: set[str]) -> Any:
    if isinstance(value, dict):
        return {k: _strip_keys_recursive(v, drop) for k, v in value.items() if k not in drop}
    if isinstance(value, list):
        return [_strip_keys_recursive(v, drop) for v in value]
    return value


def apply_shape(
    items: Iterable[Mapping[str, Any]],
    *,
    shape: LeanShape,
    verbose: bool = False,
    fields: list[str] | None = None,
    pagination: Pagination | None = None,
    pattern: str | None = None,
) -> dict[str, Any]:
    """Run the full shaping pipeline on a list of items.

    Returns ``{total, count, items}``:
        - ``total`` — items remaining after pattern filter (pre-pagination)
        - ``count`` — items in the returned page
        - ``items`` — the shaped, filtered, paginated list
    """
    materialized: list[Mapping[str, Any]] = list(items)
    if shape.extra_strip_keys:
        drop = set(shape.extra_strip_keys)
        materialized = [_strip_keys_recursive(item, drop) for item in materialized]

    filtered = filter_by_pattern(materialized, pattern, name_field=shape.name_field)
    total = len(filtered)

    projected = [_project_fields(item, fields, shape.default_fields, verbose) for item in filtered]
    if shape.drop_when_none:
        projected = [_drop_nones(item) for item in projected]

    if pagination is not None:
        page = paginate(projected, pagination.limit, pagination.offset)
    else:
        page = projected

    return {"total": total, "count": len(page), "items": page}


def shape_single(
    item: Mapping[str, Any] | None,
    *,
    shape: LeanShape,
    verbose: bool = False,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Apply whitelist + drop-None to a single item.

    Use for tools that return one record (e.g. ``get_system``). Pattern filter
    and pagination don't apply. Returns ``{}`` for ``None`` input so callers can
    chain without null checks.
    """
    if item is None:
        return {}
    if shape.extra_strip_keys:
        item = _strip_keys_recursive(item, set(shape.extra_strip_keys))
    projected = _project_fields(item, fields, shape.default_fields, verbose)
    if shape.drop_when_none:
        projected = _drop_nones(projected)
    return projected


# --- Per-tool whitelists ----------------------------------------------------
# Naming: SHAPE_<TOOL>, grouped by tool module. MSA objects carry a "health"
# and "health-reason" pair almost everywhere — always kept in the lean view
# because "is this healthy?" is the most common question asked of storage.

# system.py -----------------------------------------------------------------

SHAPE_SYSTEM = LeanShape(
    default_fields=(
        "system-name",
        "product-id",
        "product-brand",
        "midplane-serial-number",
        "health",
        "health-reason",
        "system-contact",
        "system-location",
    ),
    name_field="system-name",
)

SHAPE_VERSIONS = LeanShape(name_field="sc-fw")  # terse already; drop-None only

SHAPE_CONTROLLERS = LeanShape(
    default_fields=(
        "controller-id",
        "serial-number",
        "hardware-version",
        "sc-fw",
        "status",
        "health",
        "health-reason",
        "ip-address",
        "disks",
        "virtual-disks",
        "position",
        "redundancy-mode",
        "redundancy-status",
    ),
    name_field="controller-id",
)

SHAPE_ENCLOSURES = LeanShape(
    default_fields=(
        "enclosure-id",
        "durable-id",
        "model",
        "vendor",
        "midplane-serial-number",
        "status",
        "health",
        "health-reason",
        "enclosure-wwn",
    ),
    name_field="durable-id",
)

SHAPE_FRUS = LeanShape(
    default_fields=(
        "name",
        "description",
        "part-number",
        "serial-number",
        "fru-status",
        "fru-location",
        "enclosure-id",
    ),
)

SHAPE_LICENSE = LeanShape(name_field="feature")  # terse; drop-None only

SHAPE_NTP_STATUS = LeanShape(name_field="ntp-status")

SHAPE_NETWORK = LeanShape(
    default_fields=(
        "controller",
        "ip-address",
        "gateway",
        "subnet-mask",
        "mac-address",
        "addressing-mode",
        "link-speed",
        "duplex-mode",
        "health",
        "health-reason",
    ),
    name_field="controller",
)

# hardware.py ---------------------------------------------------------------

SHAPE_DISKS = LeanShape(
    default_fields=(
        "location",
        "serial-number",
        "vendor",
        "model",
        "size",
        "type",
        "description",
        "status",
        "health",
        "health-reason",
        "disk-group",
        "storage-pool-name",
        "usage",
        "temperature",
        "rpm",
        "ssd-life-left",
    ),
    name_field="location",
)

SHAPE_SENSORS = LeanShape(
    default_fields=(
        "sensor-name",
        "value",
        "status",
        "sensor-type",
        "container",
        "drawer-id",
    ),
    name_field="sensor-name",
)

SHAPE_FANS = LeanShape(
    default_fields=(
        "name",
        "location",
        "status",
        "speed",
        "health",
        "health-reason",
    ),
)

SHAPE_POWER_SUPPLIES = LeanShape(
    default_fields=(
        "name",
        "durable-id",
        "location",
        "model",
        "serial-number",
        "status",
        "health",
        "health-reason",
        "dc12v",
        "dc5v",
        "dctemp",
    ),
)

SHAPE_EXPANDER_STATUS = LeanShape(
    default_fields=(
        "enclosure-id",
        "drawer-id",
        "name",
        "status",
        "health",
        "health-reason",
        "sas-address",
    ),
)

# Certificate text is large and rarely needed — strip it from the lean view.
SHAPE_CERTIFICATE = LeanShape(
    default_fields=(
        "controller",
        "controller-id",
        "certificate-status",
        "time-created",
        "certificate-signature",
    ),
    name_field="controller",
    extra_strip_keys=("certificate-text",),
)

# storage.py ----------------------------------------------------------------

SHAPE_POOLS = LeanShape(
    default_fields=(
        "name",
        "serial-number",
        "storage-type",
        "total-size",
        "total-avail",
        "snap-size",
        "allocated-pages",
        "available-pages",
        "health",
        "health-reason",
    ),
)

SHAPE_DISK_GROUPS = LeanShape(
    default_fields=(
        "name",
        "serial-number",
        "pool",
        "storage-type",
        "raidtype",
        "diskcount",
        "size",
        "freespace",
        "status",
        "health",
        "health-reason",
    ),
)

SHAPE_VOLUMES = LeanShape(
    default_fields=(
        "volume-name",
        "serial-number",
        "storage-pool-name",
        "size",
        "total-size",
        "allocated-size",
        "volume-type",
        "wwn",
        "health",
        "health-reason",
    ),
    name_field="volume-name",
)

SHAPE_SNAPSHOTS = LeanShape(
    default_fields=(
        "name",
        "serial-number",
        "volume-parent",
        "source-volume",
        "snapshot-type",
        "creation-date-time",
        "total-size",
        "health",
        "health-reason",
    ),
)

# hosts.py ------------------------------------------------------------------

SHAPE_HOST_GROUPS = LeanShape(
    default_fields=(
        "name",
        "serial-number",
        "member-count",
        "durable-id",
    ),
)

SHAPE_HOSTS = LeanShape(
    default_fields=(
        "name",
        "durable-id",
        "serial-number",
        "member-count",
        "host-group",
    ),
)

SHAPE_INITIATORS = LeanShape(
    default_fields=(
        "nickname",
        "id",
        "host-bus-type",
        "discovered",
        "mapped",
        "host-id",
        "host-port-bits-a",
        "host-port-bits-b",
    ),
    name_field="nickname",
)

SHAPE_MAPS = LeanShape(
    default_fields=(
        "volume-name",
        "volume-serial",
        "identifier",
        "nickname",
        "access",
        "lun",
        "ports",
    ),
    name_field="volume-name",
)

SHAPE_PORTS = LeanShape(
    default_fields=(
        "port",
        "controller",
        "port-type",
        "target-id",
        "status",
        "health",
        "health-reason",
        "actual-speed",
        "configured-speed",
        "sfp-status",
    ),
    name_field="port",
)

# events.py -----------------------------------------------------------------

SHAPE_EVENTS = LeanShape(
    default_fields=(
        "time-stamp",
        "event-id",
        "event-code",
        "severity",
        "message",
        "additional-information",
        "recommended-action",
    ),
    name_field="event-id",
)

SHAPE_ALERTS = LeanShape(
    default_fields=(
        "alert-id",
        "severity",
        "condition",
        "detail",
        "time",
        "component-id",
        "health",
        "acknowledged",
    ),
    name_field="alert-id",
)

# statistics.py -------------------------------------------------------------
# Statistics responses are dense numeric counters; keep drop-None only and let
# fields=/verbose pick. name_field points at the object identifier so pattern
# filtering still works.

SHAPE_CONTROLLER_STATISTICS = LeanShape(name_field="durable-id")
SHAPE_VOLUME_STATISTICS = LeanShape(name_field="volume-name")
SHAPE_DISK_STATISTICS = LeanShape(name_field="serial-number")

# utility.py ----------------------------------------------------------------

# run_show_command returns arbitrary object arrays; no whitelist, drop-None only.
SHAPE_GENERIC = LeanShape()


__all__ = [
    "OBJECT_KEYS",
    # SHAPE constants
    "SHAPE_ALERTS",
    "SHAPE_CERTIFICATE",
    "SHAPE_CONTROLLERS",
    "SHAPE_CONTROLLER_STATISTICS",
    "SHAPE_DISKS",
    "SHAPE_DISK_GROUPS",
    "SHAPE_DISK_STATISTICS",
    "SHAPE_ENCLOSURES",
    "SHAPE_EVENTS",
    "SHAPE_EXPANDER_STATUS",
    "SHAPE_FANS",
    "SHAPE_FRUS",
    "SHAPE_GENERIC",
    "SHAPE_HOSTS",
    "SHAPE_HOST_GROUPS",
    "SHAPE_INITIATORS",
    "SHAPE_LICENSE",
    "SHAPE_MAPS",
    "SHAPE_NETWORK",
    "SHAPE_NTP_STATUS",
    "SHAPE_POOLS",
    "SHAPE_PORTS",
    "SHAPE_POWER_SUPPLIES",
    "SHAPE_SENSORS",
    "SHAPE_SNAPSHOTS",
    "SHAPE_SYSTEM",
    "SHAPE_VERSIONS",
    "SHAPE_VOLUMES",
    "SHAPE_VOLUME_STATISTICS",
    "LeanShape",
    "apply_shape",
    "extract_objects",
    "shape_single",
]
