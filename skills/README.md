# hpe-msa-mcp skills

Procedural knowledge for driving this MCP server. Delivered as a single, broad
skill — **`hpe-msa-engineer`** — structured as a router: a short `SKILL.md` that
points to `references/` files loaded on demand (Anthropic's large-skill pattern,
like the `pdf`/`docx` skills). Tools are the **capabilities**; the skill is the
know-how that uses them well. Keeping it in this repo means it versions with the
tools.

## Why one skill (not many)

Skills are installed one folder at a time into a shared library alongside skills
from other sources. A single `hpe-msa-engineer` skill means **one** description in
that library (not a dozen competing ones), shared domain references live **once**,
and progressive disclosure keeps context lean — only the reference a task needs is
read.

## Layout

```
hpe-msa-engineer/
├── SKILL.md                      # router + critical invariants; "read X when…"
├── references/
│   ├── health-model.md           # health/health-reason, controllers A/B, cache
│   ├── storage-model.md          # pools → disk-groups → volumes → snapshots
│   ├── hosts-mapping.md          # initiators / hosts / host-groups / maps / ports
│   ├── events-alerts.md          # alerts vs the event log; how to filter
│   ├── statistics.md             # controller / volume / disk performance
│   ├── tool-map.md               # which MCP tool for which job
│   └── playbooks/                # the 7 named task jobs
│       ├── health-check.md · capacity-review.md · disk-failure-triage.md
│       ├── mapping-audit.md · performance-review.md · event-triage.md
│       └── firmware-inventory.md
└── scripts/                      # reserved
```

## Scope

Read-only — audit, inspect, troubleshoot, explain; it never provisions, maps, or
changes array config. The server's RBAC gate is the hard enforcement regardless:
no tool above `Mode.READ` is registered, so the skill could not drive a write even
if it tried.

## Where the invariants live

The durable "how the tools behave" invariants (error ≠ empty, health lives in
fields, two controllers, rollup-first, token discipline, multi-array targeting)
live **once** in the server's `instructions` (`SERVER_INSTRUCTIONS` in
`src/hpe_msa_mcp/server.py`), which the client injects into every session.
`SKILL.md` restates a short head block so it stands alone.

## Install

The `node804-hpe-msa-mcp-setup` wizard installs this skill into
`~/.claude/skills` (Claude Code) and writes a `hpe-msa-engineer-skill.zip` for
upload to Claude Desktop / claude.ai. Or run `node804-hpe-msa-mcp-setup skill`
to (re)install just the skill. The skill is also bundled inside the published
wheel so the wizard can install it offline.

## Authoring conventions

- **Self-contained** — no references outside the skill's own folder; everything
  the skill needs is under `hpe-msa-engineer/`. References may point to sibling
  reference files by relative path.
- **Router SKILL.md** — keep it short: critical invariants + a "read this
  reference when…" table. Put depth in `references/`.
- **Versioned with the tools** — when a tool's behavior changes, update the
  matching reference in the same change.
