# Performance statistics

Three statistics tools, at three scopes. All are point-in-time counters/rates
the array maintains — a single read is a snapshot, not a trend. For a trend,
read twice with a gap and compare, and say over what interval.

## Controller statistics (`get_controller_statistics`)

Per controller (A/B):

- IOPS and bytes/sec (read + write).
- CPU load, and cache figures: **read-cache hit %** and **write-cache usage**.
- Compare A vs B: a large imbalance means one controller owns most of the busy
  volumes/pools — a balance problem, not necessarily a fault, but it caps
  headroom and worsens a failover.

## Volume statistics (`get_volume_statistics`)

Per volume — the usual starting point for "which workload is hot":

- `iops`, `bytes-per-second`, read/write split.
- **Latency**: `data-transfer-time`/average response — the number that maps to
  "it feels slow". Rising latency with flat IOPS = the backing disk-group is
  saturated.
- Cache hit ratio per volume.

Sort volumes by IOPS or latency to find the top talkers; filter with `pattern=`
to a volume name when chasing a specific complaint.

## Disk statistics (`get_disk_statistics`)

Per physical disk:

- IOPS, bytes/sec, average queue depth / I/O time.
- A single disk with latency or queue depth far above its disk-group peers is a
  **failing-but-not-yet-failed** disk — cross-check `get_events` for media
  errors on that disk and `get_disks` health. This often precedes a hard failure.

## Method for a performance review

1. Controller stats: confirm neither controller is CPU- or cache-bound, and
   check A/B balance.
2. Volume stats: rank by IOPS and by latency; identify top talkers and any
   volume whose latency is out of line.
3. Trace a hot/slow volume to its pool → disk-group → disks; disk stats reveal a
   single lagging spindle.
4. Factor in state: a disk-group mid-**reconstruct** or **scrub**
   (`get_disk_groups.current-job`) is legitimately slow — note it so a rebuild
   isn't misread as a hardware problem.

## Reporting

Give absolute numbers with units and the interval they cover. Name the top
talkers, the latency outliers, and the likely bottleneck (controller, cache,
disk-group, or a single disk). Separate "busy by design" from "degraded".
