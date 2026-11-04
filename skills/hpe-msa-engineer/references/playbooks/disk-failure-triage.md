# Playbook — disk failure / degraded disk triage

Goal: establish blast radius (is data at risk?), whether a rebuild is running or
possible, and the concrete next step.

## Steps

1. **Find the bad disks:** `get_disks`, filter to non-OK `health`. For each note
   `location`, `health-reason`, `usage`, disk-group, size/type.
2. **Assess the disk-group:** `get_disk_groups` for each affected group.
   - `raidtype` sets tolerance: RAID5 survives 1 disk, RAID6/ADAPT survive 2,
     RAID10 survives 1 per mirror.
   - `status`: `FTOL` = still fault-tolerant; `CRIT` = one more failure loses
     data; `OFFL` = already offline (data unavailable); `QTCR`/`QTOF` =
     quarantined.
   - `current-job` = `RCON` with `current-job-completion` % = **rebuilding now**.
     The group is exposed to a second failure until it hits 100%.
3. **Can it rebuild?** A non-ADAPT group needs a spare. `get_disks` for
   `usage: SPARE`/`AVAIL`; ADAPT groups use built-in spare capacity. **No spare +
   a failed disk = no automatic rebuild** — the single most important thing to
   surface.
4. **History:** `get_events` filtered to Error/Critical for the disk's location —
   recurring media-error codes confirm a genuine failure vs a transient, and
   often predate the health change.
5. **Performance signal (early warning):** `get_disk_statistics` — a disk with
   latency/queue depth far above its group peers is pre-failing even at
   `health: OK`; flag it for proactive replacement.

## Severity

- **Critical / data at risk** — group `CRIT` or `OFFL`, or a second failure in a
  single-parity group, or rebuilding with no margin.
- **Degraded but protected** — one failure in RAID6/ADAPT still `FTOL`, or a
  rebuild underway with tolerance remaining.
- **Watch** — a disk pre-failing on stats/events while still OK.

## Report

Which disks, which groups, current fault tolerance (and whether a rebuild is
running / possible), the risk if nothing changes, and the next step (replace disk
in slot X; add a spare; do not pull a second disk from group Y while it rebuilds).
Replacement is a physical/CLI action — this server can't perform it.
