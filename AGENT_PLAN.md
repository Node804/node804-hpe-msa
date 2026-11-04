# HPE MSA MCP — Agent Build Plan

> Working plan for building `node804-hpe-msa`, a token-efficient, read-only
> MCP server for HPE MSA (Modular Smart Array) storage. This document is the
> source of truth an implementing agent follows. Keep it updated as decisions
> change.

## 1. Goal & scope

Build an MCP server that exposes **read-only** visibility into HPE MSA storage
arrays through the array's REST management API, matching the conventions of the
existing suite (`panos-mcp-py`, `falcon-mcp`, etc.).

Confirmed decisions (from the requester):

| Decision | Choice |
|----------|--------|
| Tool scope | **Read-only** — only `show`-style tools; no provisioning/writes exposed. Matches the suite's default-deny posture. |
| Target hardware | **Gen6 (1060/2060/2062)** and **Gen5 (1050/2050/2052)** — REST API v3/v4, JSON-capable. |
| Build approach | Full build in one pass, matching `panos-mcp-py` structure. |

Explicitly **out of scope** (for now): any write/provisioning verb (create/delete/
map/set/restart), firmware operations, multi-array orchestration beyond a simple
target registry. Leave a gated `MSA_MODE` seam so writes *could* be added later
without re-architecting, but register nothing above `Mode.READ`.

## 2. Reference architecture (mirror `panos-mcp-py`)

The suite shares `node804-mcp-toolkit`, which provides everything cross-cutting.
Reuse it; do not reinvent:

- `Mode`, `ModeGate` — RBAC. Default-deny: unset `MSA_MODE` ⇒ `READ`. Tools are
  registered via `gate.tool(mcp, required=Mode.READ, ...)`; denied tools are
  never registered, so the client can't see them.
- `resolve_tls_config(env, prefix="MSA")` / `describe_tls` — verify-by-default TLS,
  `MSA_TLS_VERIFY` / `MSA_TLS_CA` opt-outs.
- `open_sink(env_var="MSA_AUDIT_LOG")` — JSONL audit sink (no-op when unset).
- `VerboseFlag`, `FieldsList`, `Pagination`, `Pattern` — standard lean-response params.
- `whitelist`, `strip_keys`, `paginate`, `filter_by_pattern` — pure shaping helpers.

**The one real divergence from panos:** panos wraps `pan-os-python`; MSA has no
SDK. We talk to the REST API directly over `httpx`, and MSA auth is a
**session key** (username+password → hashed login → `sessionKey` header), not a
long-lived API key. So `client.py` owns login, session-key caching, and
re-login-on-expiry instead of delegating to an SDK.

## 3. Package layout

```
hpe-msa-mcp/
  pyproject.toml            # node804-hpe-msa, console script node804-hpe-msa
  README.md                 # env table + tool catalog
  LICENSE                   # MIT
  .gitignore
  AGENT_PLAN.md             # this file
  src/hpe_msa_mcp/
    __init__.py             # __version__ = "0.1.0"; module docstring w/ 3 principles
    __main__.py             # python -m hpe_msa_mcp → server.main()
    _redact.py              # strip password / sessionKey from errors & logs
    client.py               # MSAClient: login, session cache, request(), error surface
    config/
      __init__.py
      arrays.py             # per-array registry (name/host/username in JSON, password in keyring)
    responses/
      __init__.py
      shapers.py            # per-object lean whitelists + object-key extraction
    tools/
      __init__.py
      _common.py            # ToolContext, fetch() helper, shape wiring
      status.py             # server_status (local), check_connectivity
      system.py             # system, versions, controllers, enclosures, frus, license, ntp, network
      hardware.py           # disks, sensor-status, fans, power-supplies, expander-status, certificate
      storage.py            # pools, disk-groups, volumes, snapshots
      hosts.py              # host-groups, initiators, hosts, maps, ports
      events.py             # events, alerts
      statistics.py         # controller/volume/disk statistics
      aggregations.py       # get_health_summary (rollup), count helpers
      utility.py            # run_show_command (generic, show-verb-only), list_arrays, add_array
    py.typed
  tests/
    __init__.py
    test_shapers.py
    test_config.py
    test_client_auth.py     # respx-mocked login + session reuse + re-login
  server.py is src/hpe_msa_mcp/server.py (FastMCP wiring)
```

## 4. HPE MSA REST API notes (Gen5/Gen6)

Base URL: `https://<host>/api`. All requests set header `datatype: json`.

### Authentication
1. Compute `hash = SHA-256("<username>_<password>")` (hex). Gen5/Gen6 support
   SHA-256; older firmware only MD5 — implement SHA-256 first, **fall back to
   MD5** if login returns an auth error, so mixed/older arrays still work.
2. `GET /api/login/<hash>` with `datatype: json`.
3. Response: `{"status":[{"response-type":"Success","response":"<sessionKey>", "return-code":1, ...}]}`.
   On failure `response-type` is `Error` and `return-code` is negative.
4. Subsequent calls: `GET /api/<command>/<args...>` with headers
   `sessionKey: <key>` and `datatype: json`.
5. Session expiry (idle ~30 min, or `return-code` == `-10005` / HTTP 401) ⇒
   re-login once and retry the request. Logout is `GET /api/exit` (best-effort
   on shutdown; not required).

### Response envelope
Every response is a JSON object containing:
- one or more **object arrays** keyed by object type (see key table below), and
- a trailing `"status"` array describing the command result.

Surface an error dict when the trailing status `response-type == "Error"`.

### Object-key map (key varies by command — auto-detect as fallback)
| Command | JSON key |
|---------|----------|
| `show/system` | `system` |
| `show/controllers` | `controllers` |
| `show/disks` | `drives` |
| `show/disk-groups` | `disk-groups` |
| `show/pools` | `pools` |
| `show/volumes` | `volumes` |
| `show/enclosures` | `enclosures` |
| `show/ports` | `port` |
| `show/host-groups` | `host-group` |
| `show/initiators` | `initiator` |
| `show/maps` | `volume-view` |
| `show/sensor-status` | `sensors` |
| `show/fans` | `fan` |
| `show/power-supplies` | `power-supplies` |
| `show/events` | `events` |
| `show/alerts` | `alert` (Gen6) |
| `show/versions` | `versions` |
| `show/license` | `license` |

Because keys are inconsistent across firmware, `_extract_objects(payload,
preferred_key)` should try `preferred_key`, then fall back to "first key whose
value is a list and key != 'status'". Never assume a key exists.

**Always** check `status`/`timed_out`-style signals: a partial or errored
response is not an empty inventory. Do not report "nothing configured" on an
auth/transport error — surface the error string instead.

## 5. Config & credentials (`config/arrays.py`)

Mirror `panos config/firewalls.py`, adapted for username+password:

- File: `$MSA_ARRAYS_CONFIG` or `~/.config/hpe-msa-mcp/arrays.json`.
- Stored per entry (JSON, **never the password**): `{name, host, username}`.
- Password lives in the OS keychain: `keyring` service `"hpe-msa-mcp"`,
  account = entry `name`. Auto-migrate any legacy plaintext `password` field to
  keychain on load, then rewrite the file `0o600` without it.
- Single-array env fallback when no JSON file: `MSA_HOST`, `MSA_USERNAME`,
  `MSA_PASSWORD`.
- `resolve_array(name=None)`: named entry → sole entry → env fallback → None.
  Multi-array mode (>1 entry) requires every tool to take an explicit
  `array: str` (no silent default).
- Host normalization: strip `https?://` and trailing slashes.

## 6. Client (`client.py`)

`MSAClient` per resolved array, cached for process lifetime (`_CLIENTS`):
- Fields: `name, host, username, password, tls, timeout, _session_key`.
- One `httpx.Client(verify=<from TlsConfig>, timeout=<MSA_API_TIMEOUT|30s>)`.
  - verify: `False` when TLS off; `str(ca_path)` when a CA bundle is configured;
    else `True`.
- `login()` → SHA-256 then MD5 fallback; store `_session_key`.
- `request(segments: list[str])` → ensure key, GET, parse JSON, detect expiry,
  re-login once, retry. Return parsed dict **or** a `ResolveError`-style error.
- `ResolveError` dataclass mirrors panos: tools surface `.message`, never raise
  a stack trace at the model.
- `probe_client()` for `server_status`: resolves config only, **no** network call.
- All error text passes through `_redact` (strip password + sessionKey).

Per-request timeout must be bounded (default 30–60s) so a stuck array fails fast
instead of hanging the whole MCP process — same rationale as the panos timeout cap.

## 7. Tools (all `Mode.READ`)

Every list tool takes `verbose / fields / pattern / pagination` and returns a
lean whitelist by default. Grouping:

- **status.py** — `server_status` (local: mode, tls, audit, configured arrays;
  no network), `check_connectivity` (login + `show/system` round-trip).
- **system.py** — `get_system`, `get_versions`, `get_controllers`,
  `get_enclosures`, `get_frus`, `get_license`, `get_ntp_status`, `get_network`.
- **hardware.py** — `get_disks`, `get_sensor_status`, `get_fans`,
  `get_power_supplies`, `get_expander_status`, `get_certificate`.
- **storage.py** — `get_pools`, `get_disk_groups`, `get_volumes`, `get_snapshots`.
- **hosts.py** — `get_host_groups`, `get_initiators`, `get_hosts`, `get_maps`,
  `get_ports`.
- **events.py** — `get_events` (args: severity + count, e.g. `last 100`),
  `get_alerts`.
- **statistics.py** — `get_controller_statistics`, `get_volume_statistics`,
  `get_disk_statistics`.
- **aggregations.py** — `get_health_summary`: one call that rolls up overall
  system health + degraded/failed disks + active alerts + controller redundancy,
  so the model can answer "is this array healthy?" cheaply.
- **utility.py** — `run_show_command(command, args)`: generic escape hatch,
  **hard-restricted to the `show` verb** (reject anything else) so it can't be
  used to mutate; `list_arrays`; `add_array` (writes config + keychain — this is
  a *local config* write, not an array write, so it's acceptable under read-only,
  but gate it behind `Mode.READ` and document clearly).

## 8. Server wiring (`server.py`)

- `open_sink(env_var="MSA_AUDIT_LOG")`, `resolve_tls_config(prefix="MSA")`,
  `ModeGate.from_env(env_var="MSA_MODE", default=Mode.READ, audit_sink=...)`.
- `SERVER_INSTRUCTIONS`: tight operating guidance — MSA has two controllers
  (A/B); health lives in `health`/`health-numeric` fields; prefer
  `get_health_summary` before dumping full inventories; check the response
  `status` before trusting an empty list; read-only server.
- `ctx = ToolContext(mcp, gate, tls, audit_sink)`, then `register_*_tools(ctx)`
  for every module.
- `main()`: `load_array_config()`, print version + TLS + RBAC + audit lines to
  stderr, `mcp.run()`.

## 9. Environment variables

| Var | Purpose | Default |
|-----|---------|---------|
| `MSA_MODE` | RBAC mode. Read-only surface only registers `read`. | `read` |
| `MSA_AUDIT_LOG` | Path to JSONL audit log. Unset = disabled. | unset |
| `MSA_TLS_VERIFY` | `true`/`false`. MSA controller certs are often self-signed. | `true` |
| `MSA_TLS_CA` | Path to a custom CA PEM bundle. Implies verify on. | unset |
| `MSA_ARRAYS_CONFIG` | Override path for `arrays.json`. | `~/.config/hpe-msa-mcp/arrays.json` |
| `MSA_HOST` | Single-array fallback hostname. | unset |
| `MSA_USERNAME` | Single-array fallback username. | unset |
| `MSA_PASSWORD` | Single-array fallback password. | unset |
| `MSA_API_TIMEOUT` | Per-request timeout (seconds). | `30` |

## 10. Security & correctness invariants

- **Read-only**: no tool above `Mode.READ` is registered. `run_show_command`
  rejects any verb other than `show`.
- **Secret hygiene**: password never written to JSON once keychain is available;
  every error/log line passes through `_redact` (password, `sessionKey`).
- **TLS**: verify on by default; disabling emits the toolkit's stderr warning.
- **No confident-empty**: on transport/auth/status errors, return the error, not
  an empty inventory.
- **Bounded requests**: per-request timeout cap so one stuck controller can't
  freeze the process.

## 11. Build order (task checklist)

1. Scaffold: `pyproject.toml`, `__init__.py`, `__main__.py`, `LICENSE`, `.gitignore`, `py.typed`.
2. `_redact.py` + `config/arrays.py` (+ unit test `test_config.py`).
3. `client.py` (+ `test_client_auth.py` with `respx`: login, session reuse, re-login).
4. `responses/shapers.py` + `tools/_common.py` (+ `test_shapers.py`).
5. Tool modules (status → system → hardware → storage → hosts → events →
   statistics → aggregations → utility).
6. `server.py` wiring + `README.md`.
7. Verify: `python -c "import hpe_msa_mcp.server"`, `ruff check`, `pytest`.

## 12. Open items / to confirm before/while building

- [ ] Live array host + credentials to run the `integration`-marked smoke test
      (`check_connectivity` against a real MSA). Build proceeds without it;
      creds get configured via keyring/env later.
- [ ] Confirm exact JSON object keys against the target firmware (table in §4 is
      the documented set; auto-detect covers drift, but verify on first live run).
- [x] Decide whether `add_array` belongs in the read-only surface or should be a
      one-off CLI/setup step instead of an exposed tool.
      **Decision (built):** exposed as a `Mode.READ` tool. It writes only *local
      MCP config* (the array registry + keychain), never the storage array, so it
      stays within the read-only posture toward the hardware. Documented as such
      in the tool docstring and README.

## 13. Build status (as built)

Implemented in one pass per the plan. Verified: `python -c "import
hpe_msa_mcp.server"` OK (34 tools registered), `ruff check` clean, `ruff format`
clean, `mypy --strict src` clean, `pytest` 30 passed.

**Config model (extended post-plan):** one entry per *array* (not per
controller); an entry may carry a `hosts` failover list (VIP or both controller
IPs) that the client walks in order. Shared `defaults` (username in
`arrays.json`, password in a keychain default account) let every array inherit a
common monitoring credential, with per-array override. Resolution is
per-array → default → env. New tool `set_default_credentials`; `add_array` takes
`host`/`hosts` and optional username/password (inherits defaults when omitted).

Tool surface (all `Mode.READ`): `server_status`, `check_connectivity`;
`get_system`, `get_versions`, `get_controllers`, `get_enclosures`, `get_frus`,
`get_license`, `get_ntp_status`, `get_network`; `get_disks`, `get_sensor_status`,
`get_fans`, `get_power_supplies`, `get_expander_status`, `get_certificate`;
`get_pools`, `get_disk_groups`, `get_volumes`, `get_snapshots`;
`get_host_groups`, `get_hosts`, `get_initiators`, `get_maps`, `get_ports`;
`get_events`, `get_alerts`; `get_controller_statistics`, `get_volume_statistics`,
`get_disk_statistics`; `get_health_summary`; `run_show_command`, `list_arrays`,
`set_default_credentials`, `add_array`.
