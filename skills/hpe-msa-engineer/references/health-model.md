# Health model — how an MSA reports "healthy"

## The health field

Nearly every MSA object (system, controllers, disks, pools, disk-groups,
volumes, enclosures, FRUs, fans, PSUs, sensors) carries:

- `health` — one of `OK`, `Degraded`, `Fault`, `N/A` (sometimes `Unknown`).
- `health-numeric` — the same as an integer (0 = OK) on firmware that ships it.
- `health-reason` — a human string explaining a non-OK state. **Always quote
  this** in findings; it is the actionable part.
- `health-recommended-action` — MSA's own suggested remedy, when present.

Report health from these fields, not from inferring it off other status codes.
A component with `health: OK` and a non-empty `health-reason` is still OK — the
reason is informational.

## Controllers A and B

An MSA has two controllers. `get_controllers` reports each with:

- `health` + `status` (`Operational`, `Down`, `Not Installed`).
- `redundancy-mode` / `redundancy-status` — expect `Active-Active ULP` with
  redundancy `Redundant`. `Operational but not redundant` means one controller
  or one path is missing.
- Cache state: `cache-memory-size`, `write-policy`, and whether cache is
  **mirrored** between controllers. A controller that has fallen back to
  **write-through** (because its partner or a supercap/battery failed) is a
  performance and durability warning even if `health` is OK.

**A single-controller array is degraded, not healthy** — say so explicitly even
when the surviving controller reports OK. Loss of redundancy is the headline.

## Enclosure environment

`get_sensor_status`, `get_fans`, `get_power_supplies`, `get_expander_status`
cover the physical enclosure. A failed PSU or fan, or a temperature sensor in
`Warning`/`Critical`, is a fault even when controllers and disks are OK — heat
and power problems cascade.

## What `get_health_summary` rolls up

One call returns, per array: overall system health; controller redundancy;
counts of degraded/failed disks; pool capacity/usage; and active alert count.
Use it as the first read, then drill into the specific tool only for the
dimension that came back non-OK. Don't dump full `get_disks`/`get_volumes`
inventories just to answer "is it healthy?".

## Reporting

Lead with one verdict word — **Healthy / Degraded / Fault** — then the reasons
ranked by severity (controller redundancy and failed disks first, environmental
next, informational last), each with its `health-reason`. Name anything you did
not check.
