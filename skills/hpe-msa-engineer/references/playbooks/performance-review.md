# Playbook — performance review ("is it slow?")

Goal: find the bottleneck (controller, cache, disk-group, or a single disk) and
separate "busy by design" from "degraded".

Statistics are point-in-time. For a rate/trend, read a tool twice with a gap and
compare, and state the interval. See `references/statistics.md`.

## Steps

1. **Rule out a degraded cause first.** `get_health_summary` /
   `get_disk_groups` — a group mid-**reconstruct** (`RCON`) or **scrub** (`SCRB`)
   is legitimately slow; a controller that fell to **write-through** cache
   (`get_controllers`) tanks write latency. Note either before blaming workload.
2. **Controllers:** `get_controller_statistics` — check neither controller is
   CPU- or cache-bound, and look at A/B balance. A heavy imbalance caps headroom
   and worsens failover.
3. **Volumes:** `get_volume_statistics` — rank by IOPS and by **latency**. The
   top-latency volumes are what "feels slow". Rising latency with flat IOPS =
   the backing disk-group is saturated. Use `pattern=` to zero in on a named
   volume from a complaint.
4. **Disks:** `get_disk_statistics` for the hot volume's disk-group — a single
   disk with queue depth / I/O time far above its peers is a **pre-failing**
   disk dragging the group. Cross-check `get_events` (media errors) and
   `get_disks` health.

## Diagnoses

- **Controller/cache bound** — high CPU or write-through fallback → rebalance
  ownership or fix the cache battery/supercap.
- **Disk-group saturated** — latency up, IOPS flat, all disks evenly busy → the
  spindles are maxed; needs more/faster disks or workload redistribution.
- **Single lagging disk** — one disk's stats far above peers → pre-failure;
  replace proactively.
- **Transient/expected** — rebuild/scrub in progress → slowness is temporary.

## Report

Lead with the bottleneck and whether it's load or a fault. Give absolute numbers
with units and the measurement interval, the top talkers / latency outliers, and
the recommended lever. Note anything (a running rebuild) that skewed the read.
