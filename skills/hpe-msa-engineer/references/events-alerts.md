# Events and alerts

MSA surfaces two distinct streams. Know which answers the question.

## Alerts (`get_alerts`) — Gen6, current

Active conditions the array wants a human to resolve **right now** — the storage
analog of "open tickets". Each has a severity, a condition, and usually a
recommended action. **Start here** for "what's wrong?": alerts are already
deduplicated and filtered to actionable state, so they are far cheaper and more
relevant than scraping the event log. Clearing an alert's underlying cause is the
unit of remediation.

## Events (`get_events`) — the full log

The chronological firmware event log — every state change, informational note,
and transient. It is **large**; pulling it unfiltered floods context and buries
the signal.

- Pass **severity** and a **count**: ask for the last N at or above a severity
  (e.g. last 100 Warning+). Severities: `Informational`, `Warning`, `Error`,
  `Critical` (and `Resolved` markers).
- Each event has a code, timestamp, controller, severity, and message. Recurring
  codes matter more than one-offs — a disk throwing the same media-error event
  repeatedly is failing even if `health` still reads OK.
- Correlate: a `Critical` event with a matching later `Resolved` event is
  historical, not current. An unresolved `Critical`/`Error` is live.

## Which to use

| Question | Pull |
|----------|------|
| "What needs attention now?" | `get_alerts` first; events only to add detail |
| "What happened around <time>?" | `get_events` filtered to that window/severity |
| "Is this disk/PSU flapping?" | `get_events` filtered, look for a repeating code |
| "Give me a health verdict" | `get_health_summary` (counts active alerts) then drill |

## Reporting

Lead with unresolved alerts (severity-ordered, each with its recommended
action). From the event log, surface only correlated/recurring signal — say "last
N events at Warning+" so the scope is explicit, and note when a Critical was
later Resolved so nothing reads as live that isn't.
