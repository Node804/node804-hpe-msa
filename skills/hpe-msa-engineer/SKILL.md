---
name: hpe-msa-engineer
description: >-
  Work with HPE MSA (Modular Smart Array) storage through the hpe-msa MCP server —
  check array health and controller redundancy, review pools / disk-groups /
  volumes and capacity, triage failed or degraded disks, audit host-to-volume
  mapping, review performance statistics, and triage events and alerts. Read-only
  analysis (never provisions or writes). Use for any HPE MSA question: "is this
  array healthy?", capacity planning, disk failures, mapping audits, performance,
  event/alert triage.
license: Apache-2.0
metadata:
  domain: infrastructure
  subdomain: storage
  read_only: true
---

# HPE MSA engineer

Drive the `hpe-msa` MCP server to analyze and troubleshoot HPE MSA storage
arrays (Gen5 1050/2050/2052, Gen6 1060/2060/2062) over their REST management
API. This skill is **read-only** — it inspects, audits, and explains; it never
provisions, maps, or changes array config. The server's RBAC gate enforces this
regardless (no write tool is registered).

Tool names below are bare (`get_health_summary`); your client may namespace them
(e.g. `mcp__hpe-msa__get_health_summary`). Match whatever prefix your session shows.

## Always do this first — invariants

The `hpe-msa` server states these in its own instructions too; they are the
difference between a correct answer and a confidently-wrong empty one.

1. **An error is not an empty inventory.** A tool that returns `{"error": ...}`
   hit an auth/transport/command failure. **Never** read that as "nothing
   configured" or "no disks". An empty `items` list from a *successful* call
   genuinely means none exist — check for the `error` key first.
2. **Health lives in fields, not status codes.** Component health is the `health`
   field (`OK` / `Degraded` / `Fault` / `N/A`) with a `health-reason` string.
   Prefer these over inferring from other codes.
3. **Two controllers, A and B.** An MSA is one logical array reached through
   either controller. Redundancy means both are up and cache mirroring is intact
   — a single-controller "up" array is degraded, not healthy.
4. **Lead with the rollup.** For "is this array healthy?" call
   `get_health_summary` FIRST — it rolls up system health, controller redundancy,
   degraded/failed disks, pool capacity, and active alerts in one cheap call.
5. **Spend tokens well.** Disks and volumes can number in the hundreds. Prefer
   `get_health_summary` and `pattern=` / `fields=` / `pagination` on list tools
   before pulling full inventories.
6. **Multi-array mode needs an explicit target.** When several arrays are
   configured every tool REQUIRES `array=<name>` (call `list_arrays` for names).
   With one array (or the `MSA_HOST` env fallback) it is optional.

If you are unsure which tool maps to a task, read `references/tool-map.md`.

## Choose the reference for the task

Read only what the task needs (each reference is loaded on demand):

| The task is about… | Read |
|--------------------|------|
| Health model: `health`/`health-reason`, controller redundancy, cache, sensors | `references/health-model.md` |
| Storage layout: pools, disk-groups, volumes, snapshots, capacity & provisioning | `references/storage-model.md` |
| Host access: initiators, hosts, host-groups, volume maps, ports (FC/iSCSI/SAS) | `references/hosts-mapping.md` |
| Events & alerts: severity, codes, how to pull "last N" without flooding context | `references/events-alerts.md` |
| Performance: controller / volume / disk statistics, IOPS, latency, cache hits | `references/statistics.md` |
| Which MCP tool does what; argument conventions; the `show`-only escape hatch | `references/tool-map.md` |

## Predefined playbooks

For the common named jobs, follow the matching playbook end to end:

| Ask | Playbook |
|-----|----------|
| "Is this array healthy? / health check" | `references/playbooks/health-check.md` |
| "How full is it? capacity / headroom review" | `references/playbooks/capacity-review.md` |
| "A disk failed / is degraded — what now?" | `references/playbooks/disk-failure-triage.md` |
| "Which hosts see which volumes? mapping audit" | `references/playbooks/mapping-audit.md` |
| "Is it slow? performance review" | `references/playbooks/performance-review.md` |
| "What's wrong? recent events / alert triage" | `references/playbooks/event-triage.md` |
| "What firmware / model / inventory is this?" | `references/playbooks/firmware-inventory.md` |

## General workflow

1. Confirm the target: `server_status` (local, no network) then, if needed,
   `check_connectivity`. In multi-array mode, `list_arrays` and pass `array=`.
2. Pick the playbook or reference that fits the ask.
3. Prefer `get_health_summary` for the first read; drill in only where flagged.
4. Check each result for the `error` key before trusting an empty list.
5. Report: lead with a verdict (healthy / degraded / fault), then findings ranked
   by severity, then concrete next steps. Mark any un-assessed area explicitly.
   Never propose a provisioning or mapping change — this server can't make one.

## Attribution

Original orchestration for the `hpe-msa` MCP server. Structured to mirror the
`node804` MCP suite's skill pattern (router `SKILL.md` + on-demand references).
