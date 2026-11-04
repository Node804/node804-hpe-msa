# Playbook — health check ("is this array healthy?")

Goal: a one-word verdict (Healthy / Degraded / Fault) with ranked, cited reasons.

## Steps

1. **Rollup first.** `get_health_summary`. This alone answers most of the
   question: overall health, controller redundancy, degraded/failed disk counts,
   pool capacity, active alert count. Branch on what it flags.
2. **Confirm reachability** if the summary erred: `check_connectivity`. An
   `{"error": ...}` anywhere means "unknown", never "healthy" — report the error.
3. **Controllers** (if redundancy flagged): `get_controllers`. Check both are
   Operational, redundancy is `Redundant`, and neither has dropped to
   write-through cache. A single-controller array is **Degraded** even if the
   survivor is OK. (See `references/health-model.md`.)
4. **Disks** (if degraded/failed count > 0): `get_disks` filtered to non-OK
   health. For each, quote `health-reason`; note disk-group and whether a rebuild
   is possible. Hand off to `disk-failure-triage.md` if any are Fault.
5. **Environment** (if the summary hints or for a thorough check):
   `get_sensor_status`, `get_fans`, `get_power_supplies`. A failed PSU/fan or a
   Warning/Critical sensor is a fault regardless of controller/disk state.
6. **Active alerts:** `get_alerts`. Fold unresolved alerts into the verdict,
   severity-ordered, each with its recommended action.

## Verdict rules

- **Fault** — any component `Fault`, an unresolved Critical alert, a failed PSU,
  or a disk-group `CRIT`/`OFFL`.
- **Degraded** — lost controller redundancy, write-through fallback, any
  `Degraded` component, a disk-group mid-reconstruct, or a pool over ~90%.
- **Healthy** — all health `OK`, redundancy `Redundant`, pools with headroom, no
  unresolved alerts.

## Report

Verdict word → reasons ranked (redundancy & failed disks → environment →
capacity → informational), each with its `health-reason` / recommended action →
what you did not check.
