# node804-hpe-msa-mcp

[![CI](https://github.com/Node804/node804-hpe-msa/actions/workflows/ci.yml/badge.svg)](https://github.com/Node804/node804-hpe-msa/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/node804-hpe-msa-mcp)](https://pypi.org/project/node804-hpe-msa-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/node804-hpe-msa-mcp)](https://pypi.org/project/node804-hpe-msa-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A token-efficient, **read-only** [Model Context Protocol](https://modelcontextprotocol.io)
server for **HPE MSA** (Modular Smart Array) storage — Gen5 (1050/2050/2052) and
Gen6 (1060/2060/2062). It exposes the array's `show`-style visibility through the
MSA REST management API, shaped for minimal token use.

Part of the Node804 MCP suite; shares
[`node804-mcp-toolkit`](../mcp-toolkit-py) for RBAC, TLS, audit, and response
shaping, and mirrors the conventions of `node804-panos-mcp`.

## Design principles

1. **Lean responses by default** — each tool returns a curated field whitelist;
   `verbose=true` opts into the full response. Typically 40–70% fewer tokens.
2. **Read-only by construction** — every tool is a `show` command. No
   provisioning/write tool is registered, ever. `MSA_MODE` never registers a
   surface above `read`.
3. **TLS verification on by default** — MSA controller certificates are often
   self-signed; disabling verification is an explicit opt-in that warns.
4. **No confident-empty** — a failed auth/transport/command returns an `error`,
   never a misleading empty inventory.

## HPE MSA engineer skill

The repo also ships a skill, [`skills/hpe-msa-engineer/SKILL.md`](skills/hpe-msa-engineer/SKILL.md),
that teaches the model how to work with an MSA through these tools: to reach for
`get_health_summary` before dumping inventories, to read health from the
`health`/`health-reason` fields, to treat an `error` result as "unknown" rather
than "empty", and to follow named playbooks (health check, capacity review, disk
failure triage, mapping audit, performance review, event/alert triage, firmware
inventory). The server works without it, but the model makes fewer calls and
fewer mistakes with it. The setup wizard installs it for you (below), and it is
bundled inside the published wheel so the wizard can install it offline.

## Installation

Requires Python ≥ 3.13.

**Option A — uv (recommended).** No install step: `uvx` fetches the package from
PyPI on first run and caches it. Pin the version so upgrades are deliberate:

```bash
uvx node804-hpe-msa-mcp==0.1.0
```

**Option B — pip:**

```bash
pip install node804-hpe-msa-mcp
```

**Option C — from source (development):**

```bash
git clone https://github.com/Node804/node804-hpe-msa.git
cd node804-hpe-msa
uv sync          # or: pip install -e ".[dev]"
```

Each option provides the `node804-hpe-msa-mcp` command (and the
`node804-hpe-msa-mcp-setup` wizard), plus the `hpe_msa_mcp` Python module
(runnable as `python -m hpe_msa_mcp`). The package pulls in its dependency
[`node804-mcp-toolkit`](https://pypi.org/project/node804-mcp-toolkit/) — shared
RBAC, audit-logging, TLS, and lean-response utilities — automatically.

On Windows, a pip/venv install puts the console scripts at
`.venv\Scripts\node804-hpe-msa-mcp.exe` (and `...-setup.exe`); on Linux/macOS at
`.venv/bin/node804-hpe-msa-mcp`. When configuring an MCP host, use the command's
absolute path (or run it via `uvx`) — see the [Claude Desktop](#claude-desktop)
section for a full Windows example.

## Quick setup (wizard)

After a pip install, the fastest way to get configured is the bundled wizard:

```bash
node804-hpe-msa-mcp-setup
```

> **If `node804-hpe-msa-mcp-setup` isn't found**, the console script isn't on
> your `PATH` (common with a pip `--user` install or a venv that isn't active).
> Use the module form instead — it's identical and works for every subcommand
> below:
>
> ```bash
> python -m hpe_msa_mcp.setup
> ```
>
> Use the full interpreter path if `python` isn't the one you installed into,
> e.g. `C:\Python313\python.exe -m hpe_msa_mcp.setup`.

The wizard walks through everything the server needs:

1. an **`arrays.json` entry** (name + host, or a comma-separated failover list);
2. the **controller password**, read with `getpass` and stored straight in the OS
   keychain — never written to disk;
3. optional **shared default credentials** (one monitoring user+password reused by
   every array that has none of its own);
4. a **Claude Desktop** server entry pointing at this interpreter, with no host or
   password in the config (both resolve from 1/2); and
5. installing the **`hpe-msa-engineer` skill** — copied into `~/.claude/skills`
   for Claude Code, and zipped for upload to Claude Desktop / claude.ai.

Nothing sensitive is echoed, and the Claude Desktop config is backed up before it
is touched. For a scripted, non-interactive install, provide the password in the
`MSA_SETUP_PASSWORD` environment variable and pass `--yes`:

```bash
MSA_SETUP_PASSWORD='...' node804-hpe-msa-mcp-setup \
  --name prod --host 10.0.0.10,10.0.0.11 --username monitor \
  --claude --skill --yes
```

Run `node804-hpe-msa-mcp-setup --help` (or `python -m hpe_msa_mcp.setup --help`)
for every flag. The wizard is **modify-aware**: when `arrays.json` already has
entries it lists them and lets you skip straight to the client/skill steps, so
re-running it never forces you to re-enter an array. It also exposes subcommands
for day-to-day changes:

```bash
# Upgrade just the skill (after pip install -U node804-hpe-msa-mcp)
node804-hpe-msa-mcp-setup skill

# Manage arrays.json without the full wizard
node804-hpe-msa-mcp-setup array list
node804-hpe-msa-mcp-setup array add --name branch --host 10.0.0.30
node804-hpe-msa-mcp-setup array remove --name branch
```

## Configuration

### Credentials

Two sources, in priority order:

1. **`arrays.json`** (multi-array) — at `$MSA_ARRAYS_CONFIG`, or the default
   location:
   - Linux/macOS: `~/.config/hpe-msa-mcp/arrays.json`
   - Windows: `%USERPROFILE%\.config\hpe-msa-mcp\arrays.json`
     (e.g. `C:\Users\you\.config\hpe-msa-mcp\arrays.json`)

   Passwords live in the OS keychain (service `hpe-msa-mcp` — on Windows this is
   Credential Manager); the file holds only non-secret fields. A plaintext
   `password` in the file (per-array or in `defaults`) is auto-migrated to the
   keychain on first load and stripped from the file.

   **One entry per array, not per controller.** An MSA is a single logical
   management target — logging into either controller returns the whole array,
   so a second entry for controller B would just be a duplicate. To survive a
   controller's management port going down, either point `host` at the array's
   **management VIP**, or give one entry a `hosts` list (e.g. both controller
   IPs); the client tries them in order and settles on the first that answers.

   ```json
   {
     "arrays": [
       { "name": "san-a", "host": "10.0.0.10", "username": "manage" },
       { "name": "san-b", "hosts": ["10.0.0.20", "10.0.0.21"], "username": "manage" }
     ]
   }
   ```

   **Shared credentials (same monitoring user on every array).** Set the
   username + password once as shared defaults, then list just `{ name, host }`
   per array — each inherits the defaults, and any entry can override
   `username` (or set its own keychain password). Resolution is
   per-array → default → env:

   ```json
   {
     "defaults": { "username": "monitor" },
     "arrays": [
       { "name": "san-a", "host": "10.0.0.10" },
       { "name": "san-b", "hosts": ["10.0.0.20", "10.0.0.21"] },
       { "name": "san-c", "host": "10.0.0.30", "username": "admin" }
     ]
   }
   ```

   The shared default **password** is never stored in the file — it lives in the
   keychain default account. Set it (and the default username), and register
   arrays with just a name + host, using the
   [`node804-hpe-msa-mcp-setup`](#quick-setup-wizard) wizard. Or, for a fully
   env-driven setup, leave `defaults.username` out and set `MSA_USERNAME` +
   `MSA_PASSWORD` — they act as the shared default for every array entry that
   doesn't specify its own.

2. **Environment fallback** (single array) — used when no `arrays.json` exists:

   Linux/macOS:
   ```bash
   export MSA_HOST=10.0.0.10
   export MSA_USERNAME=manage
   export MSA_PASSWORD=...    # prefer arrays.json + keychain in production
   ```

   Windows (PowerShell):
   ```powershell
   $env:MSA_HOST = "10.0.0.10"
   $env:MSA_USERNAME = "manage"
   $env:MSA_PASSWORD = "..."   # prefer arrays.json + keychain in production
   ```

   In practice, an MCP host passes these via its own `env` block (see below)
   rather than the shell.

When more than one array is configured, every tool **requires** the `array`
parameter. With one array (or the env fallback) it's optional.

### Environment variables

| Var                   | Purpose                                                  | Default |
|-----------------------|----------------------------------------------------------|---------|
| `MSA_MODE`            | RBAC mode. Only `read` registers tools.                  | `read`  |
| `MSA_AUDIT_LOG`       | Path to a JSON-lines audit log. Unset = disabled.        | unset   |
| `MSA_TLS_VERIFY`      | `true` / `false`. MSA certs are often self-signed.       | `true`  |
| `MSA_TLS_CA`          | Path to a custom CA PEM bundle. Implies verify on.       | unset   |
| `MSA_ARRAYS_CONFIG`   | Override path for `arrays.json`.                          | `~/.config/hpe-msa-mcp/arrays.json` |
| `MSA_HOST`            | Single-array fallback hostname.                          | unset   |
| `MSA_USERNAME`        | Single-array fallback username.                          | unset   |
| `MSA_PASSWORD`        | Single-array fallback password.                          | unset   |
| `MSA_API_TIMEOUT`     | Per-request timeout in seconds.                          | `30`    |

### MCP host config (generic)

If the console script is on the host's `PATH`:

```json
{
  "mcpServers": {
    "hpe-msa": {
      "command": "node804-hpe-msa-mcp",
      "env": { "MSA_HOST": "10.0.0.10", "MSA_USERNAME": "manage", "MSA_PASSWORD": "..." }
    }
  }
}
```

Or run as a module: `python -m hpe_msa_mcp`.

## Claude Desktop

Claude Desktop does not use your shell environment, so point `command` at the
**absolute path** of the console script (or the venv Python) and pass
credentials in the `env` block. Edit the config file, then fully quit and
reopen Claude Desktop.

Config file location:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
  (e.g. `C:\Users\you\AppData\Roaming\Claude\claude_desktop_config.json`)
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

You can open the file quickly from Claude Desktop:
**Settings → Developer → Edit Config**.

### Windows example

Use the console script's absolute path. In JSON, backslashes must be escaped
(`\\`) — or use forward slashes, which also work on Windows.

```json
{
  "mcpServers": {
    "hpe-msa": {
      "command": "C:\\Users\\you\\Repos\\MCP\\hpe-msa-mcp\\.venv\\Scripts\\node804-hpe-msa-mcp.exe",
      "env": {
        "MSA_HOST": "10.0.0.10",
        "MSA_USERNAME": "manage",
        "MSA_PASSWORD": "your-password",
        "MSA_TLS_VERIFY": "false"
      }
    }
  }
}
```

Alternatively, invoke the module through the venv's Python (handy if the
`.exe` shim is missing):

```json
{
  "mcpServers": {
    "hpe-msa": {
      "command": "C:\\Users\\you\\Repos\\MCP\\hpe-msa-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "hpe_msa_mcp"],
      "env": {
        "MSA_HOST": "10.0.0.10",
        "MSA_USERNAME": "manage",
        "MSA_PASSWORD": "your-password"
      }
    }
  }
}
```

### macOS example

```json
{
  "mcpServers": {
    "hpe-msa": {
      "command": "/Users/you/Repos/MCP/hpe-msa-mcp/.venv/bin/node804-hpe-msa-mcp",
      "env": {
        "MSA_HOST": "10.0.0.10",
        "MSA_USERNAME": "manage",
        "MSA_PASSWORD": "your-password"
      }
    }
  }
}
```

Notes:

- Prefer `arrays.json` + the OS keychain over a plaintext `MSA_PASSWORD` in the
  config. If you configure `arrays.json`, you can drop the credential env vars
  entirely (keep `command`/`args`), and reference arrays by name via the `array`
  tool parameter.
- `MSA_TLS_VERIFY` defaults to `true`. MSA controller certificates are commonly
  self-signed — either set `MSA_TLS_VERIFY=false` (as above) or point
  `MSA_TLS_CA` at your internal CA bundle.
- After a successful start you should see the tools available in Claude Desktop;
  run `server_status` to confirm posture and `check_connectivity` to test the
  array round-trip.

## Authentication model

MSA uses a **session key**, not a long-lived API key. The client hashes
`username_password` (SHA-256, with an MD5 fallback for old firmware),
`GET`s `/api/login/<hash>`, and caches the returned `sessionKey`. The key idles
out (~30 min); an expired-session response transparently re-logs-in and retries.
Login hashes, session keys, and passwords are scrubbed from every error/log line.

## Tool catalog

All tools are `Mode.READ`. List tools accept `verbose` / `fields` / `pattern` /
`pagination` and return `{ total, count, items }`.

### Diagnostics
- **`server_status`** — active mode, TLS posture, audit sink, configured arrays. No network call.
- **`check_connectivity`** — live login + `show system` round-trip.

### System
- **`get_system`**, **`get_versions`**, **`get_controllers`**, **`get_enclosures`**,
  **`get_frus`**, **`get_license`**, **`get_ntp_status`**, **`get_network`**

### Hardware
- **`get_disks`**, **`get_sensor_status`**, **`get_fans`**, **`get_power_supplies`**,
  **`get_expander_status`**, **`get_certificate`**

### Storage
- **`get_pools`**, **`get_disk_groups`**, **`get_volumes`**, **`get_snapshots`**

### Hosts / SAN
- **`get_host_groups`**, **`get_hosts`**, **`get_initiators`**, **`get_maps`**, **`get_ports`**

### Events
- **`get_events`** (count + optional severity), **`get_alerts`**

### Statistics
- **`get_controller_statistics`**, **`get_volume_statistics`**, **`get_disk_statistics`**

### Aggregation
- **`get_health_summary`** — one-call rollup of system health, controller
  redundancy, degraded/failed disks, pool capacity, and active alerts. Start here
  for "is this array healthy?".

### Utility
- **`run_show_command`** — run any read-only `show` command not covered above.
  The `show` verb is fixed by the server; the command and arguments are validated
  against a safe token charset, so it can never mutate the array.
- **`list_arrays`** — configured targets + shared defaults (never passwords).

> **Configuring arrays is not an MCP tool.** Registering arrays and setting shared
> credentials mutate local config + the OS keychain, and exposing that to the
> model is a prompt-injection risk (a malicious instruction could register an
> attacker host that inherits your monitoring credential and leak it on the next
> login). Use the [`node804-hpe-msa-mcp-setup`](#quick-setup-wizard) wizard —
> `array add` / `array remove` and shared-default setup live there, run by you.

## Development

```bash
pytest            # unit tests (respx-mocked; no live array needed)
ruff check .
ruff format .
mypy src
```

Live smoke tests are marked `integration` and skipped by default; point them at
a real array by configuring credentials and running `pytest -m integration`.

### Installing the skill manually

The setup wizard installs the [`hpe-msa-engineer`](skills/hpe-msa-engineer/SKILL.md)
skill, but you can also do it by hand. For **Claude Code**, copy the folder into
your personal skills directory (or a project's `.claude/skills/` to scope it there):

```bash
mkdir -p ~/.claude/skills && cp -r skills/hpe-msa-engineer ~/.claude/skills/
```

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
Copy-Item -Recurse skills\hpe-msa-engineer "$env:USERPROFILE\.claude\skills\"
```

For **Claude Desktop / claude.ai**, zip the folder (the archive must contain
`hpe-msa-engineer/SKILL.md`) and upload it under **Settings → Capabilities →
Skills** — or just run `node804-hpe-msa-mcp-setup skill`, which writes the zip for
you. After upgrading the server, update the skill too; it describes tool behavior
for the matching version.

## License

MIT © 2026 Michael Pope
