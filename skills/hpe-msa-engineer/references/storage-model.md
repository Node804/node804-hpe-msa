# Storage model — pools, disk-groups, volumes, snapshots

MSA virtual storage is layered:

```
physical disks → disk-groups (RAID sets) → pool (A or B) → volumes → maps → hosts
```

## Pools (`get_pools`)

A virtual MSA has up to two pools, **A** and **B**, each owned by the
same-letter controller. Key fields:

- `total-size`, `total-avail`, `allocated-size` (or `total-size` /
  `snap-size` / overcommit on thin pools).
- `health` + `health-reason`.
- `overcommit` — whether thin provisioning may allocate beyond physical.

Capacity math: **used %** = allocated / total. Flag a pool over ~80% as a
headroom risk, over ~90% as urgent — a full pool stops writes to thin volumes.
Report both pools; they fill independently and one can be critical while the
other is idle.

## Disk-groups (`get_disk_groups`)

The RAID sets that back a pool. Fields that matter:

- `raidtype` (RAID1/5/6/10/ADAPT), `diskcount`, `size`.
- `health` + `health-reason`, `status` (`FTOL` fault-tolerant online, `CRIT`,
  `OFFL`, `QTCR`/`QTOF` quarantined).
- Activity: `current-job` (`RCON` reconstruct/rebuild, `VRSC`/`SCRB` scrub,
  `EXPD` expand, `INIT`) + `current-job-completion` %. A group mid-**reconstruct**
  is exposed to a second failure until it finishes — call this out.
- `adapt-*` fields on ADAPT groups (spare capacity, target spare).

RAID level sets fault tolerance: RAID5 survives 1 disk, RAID6/ADAPT survive 2,
RAID10 survives 1 per mirror. Match that against failed/degraded disk counts.

## Volumes (`get_volumes`)

- `size`, `total-size`, `allocated-size`; `blocksize`.
- `storage-pool-name` / owning pool; `volume-type` (base, snapshot).
- `health` + `health-reason`.
- Thin vs thick: a thin volume's `allocated-size` grows on write; compare to
  `size` for real consumption.

## Snapshots (`get_snapshots`)

- Source volume, creation time, `snapshot-retention-priority`.
- Snapshot space draws from the pool — heavy snapshot churn is a common hidden
  cause of pool fill. Cross-check snapshot count/size against pool headroom.

## Spares

Global and dynamic spares back reconstruction. No disk dedicated-/global-spare
plus a failed disk in a non-ADAPT group = **no automatic rebuild** until a spare
or replacement appears. Check `get_disks` for `usage: SPARE` / `AVAIL` and ADAPT
groups' built-in spare capacity.

## Reporting capacity

Give per-pool used/total and %, name the fullest pool, attribute growth
(volumes vs snapshots) when you can, and state headroom in both % and absolute
free space.
