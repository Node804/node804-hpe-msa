# Host access — initiators, hosts, host-groups, maps, ports

MSA host access is a hierarchy that mirrors the storage side:

```
initiator (WWPN / IQN)  →  host  →  host-group (cluster)
                                         │
volume  ──────────── map (LUN + access + ports) ──────────┘
```

## Initiators (`get_initiators`)

The raw endpoints that log in:

- `id` — WWPN (FC/SAS) or IQN (iSCSI).
- `nickname` — set when the initiator has been named into a host; blank for
  **discovered** (logged-in but unconfigured) initiators.
- `host-bus-type` (`FC`, `iSCSI`, `SAS`).

A discovered-but-unnamed initiator with active I/O is often a host that was
cabled/zoned but never mapped — flag it.

## Hosts (`get_hosts`) and host-groups (`get_host_groups`)

A **host** groups one or more initiators (a server with dual HBAs = two
initiators, one host). A **host-group** groups hosts (a cluster). Mapping a
volume to a host-group maps it to every member — the usual pattern for clustered
access. Check membership before trusting a map's reach.

## Maps (`get_maps`)

The volume ↔ host relationship. Per mapping:

- Volume + the host/host-group/initiator it is presented to.
- `lun` — the LUN number the host sees.
- `access` — `read-write`, `read-only`, or `no-access` (an explicit mask).
- `ports` — which controller host-ports expose it.

Watch for: the same LUN number reused inconsistently across hosts in a cluster
(can confuse multipath), `no-access` overrides hiding a volume, and volumes with
**no map at all** (orphaned capacity — allocated but unreachable).

## Ports (`get_ports`)

Controller host-facing ports:

- `port`, `port-type` (`FC`/`iSCSI`/`SAS`), `status` (`Up`/`Disconnected`),
  `configured-speed` / `actual-speed`.
- Target identifiers (target WWPN/IQN).

For redundancy, each host should reach a volume through ports on **both**
controllers. A host homed to one controller's ports loses access on a controller
failover — note single-controller paths in a mapping audit.

## Reporting a mapping audit

Build the volume → host(-group) → initiator chain, note LUN/access per map, and
flag: unmapped volumes, discovered initiators never named, single-controller
paths, and `no-access` masks. Distinguish "configured" from "currently
connected" (port `status`).
