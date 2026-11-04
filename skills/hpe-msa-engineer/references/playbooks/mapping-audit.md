# Playbook — host-to-volume mapping audit

Goal: a clear picture of which hosts see which volumes, at which LUN/access, over
which paths — and the anomalies (orphans, masks, single-controller paths).

## Steps

1. **Maps:** `get_maps` — the volume ↔ host/host-group/initiator relationships
   with `lun`, `access`, and `ports`.
2. **Host side:** `get_hosts` and `get_host_groups` to expand a host-group map to
   its member hosts, and `get_initiators` to resolve each host to its WWPNs/IQNs.
3. **Volume side:** `get_volumes` for the full volume list — any volume absent
   from `get_maps` is **unmapped** (allocated but unreachable capacity).
4. **Ports:** `get_ports` — confirm each mapping's ports exist on **both**
   controllers and are `Up`; a volume reachable only through one controller's
   ports loses access on failover.

## Build the chain

For each volume: `volume → host-group/host → initiator(s)`, with LUN, access, and
exposing ports. Present it as a table.

## Flag

- **Unmapped volumes** — capacity no host can reach.
- **Discovered, unnamed initiators** (`get_initiators`, blank nickname) with
  activity — cabled/zoned but never mapped into a host.
- **Inconsistent LUNs** for the same volume across cluster members — can break
  multipath/clustering.
- **`no-access` masks** hiding a volume from a host that would otherwise inherit
  it from a group map.
- **Single-controller paths** — mapping exposed only through one controller's
  ports (no failover redundancy).

## Report

The mapping table, then the anomaly list ranked by impact (unreachable data and
broken redundancy first). Recommend fixes but don't apply them — this server
makes no mapping changes.
