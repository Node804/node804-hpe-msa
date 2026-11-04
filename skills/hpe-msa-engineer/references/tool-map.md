# Tool map — which `hpe-msa` tool for which job

Every list tool takes the lean-response params: `verbose` (default false → lean
whitelist; true → full raw object), `fields` (comma list to project), `pattern`
(substring/glob filter), and `pagination`. Every array-touching tool takes an
optional `array=<name>` — **required** when more than one array is configured.
A tool that fails returns `{"error": "..."}`; check that before trusting output.

## Diagnostics (always available)

| Tool | Use | Network? |
|------|-----|----------|
| `server_status` | Mode, TLS posture, audit sink, configured arrays, defaults. First call for "how is this server set up?" | No |
| `check_connectivity` | Login + `show system` round-trip against the array. "Can we reach it?" | Yes |

## Rollup — reach for this first

| Tool | Use |
|------|-----|
| `get_health_summary` | One-call rollup: system health, controller redundancy, degraded/failed disks, pool capacity, active alerts. Always the first read for "is it healthy?". |

## System & identity

| Tool | Shows |
|------|-------|
| `get_system` | System name, model, world-wide name, overall health/health-reason |
| `get_versions` | Firmware/bundle versions per controller |
| `get_controllers` | Controllers A/B: health, status, redundancy mode, cache |
| `get_enclosures` | Enclosure inventory + health |
| `get_frus` | Field-replaceable units (enclosure FRUs) |
| `get_license` | Licensed features |
| `get_ntp_status` | NTP state / time sync |
| `get_network` | Controller management network parameters |

## Hardware health

| Tool | Shows |
|------|-------|
| `get_disks` | Physical disks: health, type, size, RPM/SSD, disk-group membership |
| `get_sensor_status` | Temperature / voltage / current sensors |
| `get_fans` | Fan modules + health/speed |
| `get_power_supplies` | PSUs + health |
| `get_expander_status` | SAS expander status |
| `get_certificate` | Controller HTTPS certificate status |

## Storage layout

| Tool | Shows |
|------|-------|
| `get_pools` | Virtual pools: capacity, used/available, health |
| `get_disk_groups` | Disk groups (RAID sets): RAID level, health, pool, rebuild/scrub |
| `get_volumes` | Volumes: size, pool, thin/thick, health |
| `get_snapshots` | Snapshots + source volume + retention |

## Host access

| Tool | Shows |
|------|-------|
| `get_host_groups` | Host groups (clusters) |
| `get_hosts` | Hosts and their initiators |
| `get_initiators` | Initiators (WWPN/IQN), discovered vs named |
| `get_maps` | Volume ↔ host/host-group mappings (LUN, access, ports) |
| `get_ports` | Host-facing controller ports (FC/iSCSI/SAS), link status |

## Events, alerts, performance

| Tool | Shows |
|------|-------|
| `get_events` | Event log. Takes severity + count (e.g. last 100); filter hard — the log is large |
| `get_alerts` | Active alerts (Gen6) needing attention |
| `get_controller_statistics` | Per-controller IOPS, throughput, cache |
| `get_volume_statistics` | Per-volume IOPS, throughput, latency |
| `get_disk_statistics` | Per-disk IOPS, throughput, queue depth |

## Escape hatch & local config

| Tool | Use | Writes? |
|------|-----|---------|
| `run_show_command` | Generic `show`-verb passthrough for a command with no dedicated tool. **Rejects any verb other than `show`** — cannot mutate the array. | No (array) |
| `list_arrays` | List configured array names + hosts + shared defaults (never passwords) | No |

Registering arrays and setting shared credentials are **not** MCP tools —
exposing config/keychain mutation to the model is a prompt-injection risk. If the
user asks to add or change an array, point them at the `node804-hpe-msa-setup`
wizard (`array add` / `array remove`, or shared-default setup); don't look for a
tool to do it.
