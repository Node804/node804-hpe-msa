# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-11-03

Initial release. A token-efficient, read-only MCP server for HPE MSA (Modular
Smart Array) storage — Gen5 (1050/2050/2052) and Gen6 (1060/2060/2062) — over the
array's REST management API.

### Added — tool surface

33 read-only tools, all `Mode.READ`. List tools return default-lean field
whitelists (`verbose=true` to opt out) with `fields` / `pattern` / `pagination`.

- **Diagnostics** — `server_status` (local; no network), `check_connectivity`.
- **System** — `get_system`, `get_versions`, `get_controllers`, `get_enclosures`,
  `get_frus`, `get_license`, `get_ntp_status`, `get_network`.
- **Hardware** — `get_disks`, `get_sensor_status`, `get_fans`,
  `get_power_supplies`, `get_expander_status`, `get_certificate`.
- **Storage** — `get_pools`, `get_disk_groups`, `get_volumes`, `get_snapshots`.
- **Hosts / SAN** — `get_host_groups`, `get_hosts`, `get_initiators`, `get_maps`,
  `get_ports`.
- **Events** — `get_events`, `get_alerts`.
- **Statistics** — `get_controller_statistics`, `get_volume_statistics`,
  `get_disk_statistics`.
- **Aggregation** — `get_health_summary`, a one-call rollup of system health,
  controller redundancy, degraded/failed disks, pool capacity, and active alerts.
- **Utility** — `run_show_command` (hard-restricted to the `show` verb) and
  `list_arrays`. Registering arrays and setting shared credentials are
  intentionally **not** MCP tools (config/keychain mutation from a prompt is a
  credential-exfil risk); those live only in the setup wizard CLI.

### Added — configuration & security

- Multi-array registry (`arrays.json`): one entry per array, with an optional
  `hosts` failover list (VIP or both controller IPs) walked in order.
- OS-keychain credential storage (service `hpe-msa-mcp`); plaintext passwords in
  the file are auto-migrated to the keychain and stripped on load.
- Shared default credentials (one monitoring user+password reused by arrays with
  none of their own); resolution is per-array → default → env.
- Session-key authentication (SHA-256 with MD5 fallback) with transparent
  re-login on expiry; login hashes, session keys, and passwords redacted from
  every error and log line.
- Default-deny RBAC (`MSA_MODE`, read-only surface), verify-by-default TLS
  (`MSA_TLS_VERIFY` / `MSA_TLS_CA`), and an optional JSONL audit log
  (`MSA_AUDIT_LOG`).

### Added — `node804-hpe-msa-setup` install wizard

A bundled wizard that configures the server end to end, so a new install no
longer means hand-editing JSON. Run `node804-hpe-msa-setup` (or
`python -m hpe_msa_mcp.setup` if the console script isn't on `PATH`).

- Stores the controller password in the OS keychain (read with `getpass`,
  confirmed by retype, never written to disk) and writes only non-secret fields
  to `arrays.json`. Guards against terminals where `getpass` mis-reads (warns on
  the insecure fallback and on a suspiciously short value); `--password-stdin`
  offers a terminal-agnostic alternative.
- Supports a comma-separated failover host list and optional shared defaults.
- Wires a Claude Desktop server entry (host and password resolve from
  `arrays.json` + keychain, so neither lands in the client config), backing up the
  existing config first.
- Installs the `hpe-msa-engineer` skill into `~/.claude/skills` and writes an
  upload zip for Claude Desktop / claude.ai.
- Modify-aware, with `array list|add|remove` and `skill` subcommands; a
  non-interactive mode (`MSA_SETUP_PASSWORD` + `--yes`) for scripted installs.

### Added — `hpe-msa-engineer` skill pack

Procedural knowledge for driving the server well, bundled inside the wheel so the
wizard can install it offline. A router `SKILL.md` plus on-demand references
(health model, storage model, host mapping, events/alerts, statistics, tool map)
and seven playbooks (health check, capacity review, disk failure triage, mapping
audit, performance review, event triage, firmware inventory).

### Added — packaging & CI

- Console scripts `node804-hpe-msa` and `node804-hpe-msa-setup`.
- GitHub Actions: CI (ruff, ruff format, mypy `--strict`, pytest on Python 3.13
  and 3.14) and PyPI publishing via Trusted Publishing (OIDC) on `v*` tags.
- Pre-commit hooks (ruff) pinned to the same ruff version as CI.

[Unreleased]: https://github.com/Node804/node804-hpe-msa/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Node804/node804-hpe-msa/releases/tag/v0.1.0
