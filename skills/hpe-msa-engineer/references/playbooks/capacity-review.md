# Playbook — capacity / headroom review

Goal: how full is the array, which pool is tightest, what's driving growth, and
how much runway remains.

## Steps

1. **Rollup:** `get_health_summary` for the quick per-pool capacity figures.
2. **Pools:** `get_pools`. For each (A and B), compute **used %** =
   allocated / total and absolute free space. Pools fill independently — report
   both; one can be critical while the other is idle. Note `overcommit` (thin).
3. **Attribute the usage:**
   - `get_volumes` — rank by `allocated-size`; on thin volumes compare
     `allocated-size` to `size` for real consumption vs provisioned.
   - `get_snapshots` — snapshot space draws from the pool and is the most common
     *hidden* cause of fill. Sum snapshot footprint per pool and call it out
     separately from volume data.
4. **Disk-group backing:** `get_disk_groups` — confirm whether a tight pool can
   be expanded (free disks / ADAPT spare capacity) or is maxed on its spindles.
5. **Spares/expansion room:** `get_disks` for `AVAIL` disks that could extend a
   group.

## Thresholds

- **> 80% used** — plan headroom now.
- **> 90% used** — urgent; a full thin pool stops writes and can take volumes
  offline.
- Flag any pool whose snapshot footprint rivals its volume data — snapshot
  retention is usually the cheapest lever.

## Report

Per-pool used/total/% and free space; name the fullest pool; split growth into
volume data vs snapshots; state runway (absolute free + whether the backing
disk-groups can expand); recommend the lever (reclaim snapshots, add disks,
migrate a volume). This server can't make the change — recommend, don't act.
