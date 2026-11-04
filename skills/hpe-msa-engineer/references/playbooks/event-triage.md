# Playbook — event / alert triage ("what's wrong?")

Goal: separate what needs attention now from historical noise, with each live
item tied to a cause and a next step.

## Steps

1. **Active alerts first:** `get_alerts` (Gen6). These are already deduplicated to
   actionable state and carry recommended actions — the cheapest, highest-signal
   read. Rank by severity.
2. **Scope the event log deliberately:** `get_events` with a **severity floor**
   and a **count** (e.g. last 100 at Warning+). Never pull it unfiltered — it is
   large and buries the signal. (See `references/events-alerts.md`.)
3. **Correlate, don't list:**
   - Match each `Critical`/`Error` to any later `Resolved` marker — resolved =
     historical, not live.
   - Group by event code + component. A **recurring** code (same disk media
     error, same PSU flap) is a real trend even when `health` still reads OK;
     a one-off transient usually isn't.
4. **Tie to hardware state:** for a recurring code, pull the matching tool
   (`get_disks`, `get_power_supplies`, `get_controllers`) to confirm whether the
   component's health has moved yet.

## Classify

- **Live / act now** — unresolved Critical/Error alert or event with a confirmed
  component impact.
- **Trend / watch** — a recurring Warning+ code not yet reflected in health.
- **Historical** — Critical/Error with a matching Resolved, or an isolated
  transient.

## Report

Unresolved alerts first (severity-ordered, each with recommended action), then
correlated event trends (name the code and how many times over what window),
then a short "historical / resolved" note so nothing stale reads as current.
State the event scope you pulled ("last N at Warning+") so coverage is explicit.
