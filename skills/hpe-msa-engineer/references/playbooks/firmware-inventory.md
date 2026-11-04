# Playbook — firmware / model / inventory

Goal: a clean inventory of what this array *is* — model, identity, firmware per
controller, enclosures, FRUs, licensing, and time/network posture — for asset
records, support cases, or an upgrade-readiness check.

## Steps

1. **Identity:** `get_system` — system name, model (1050/2050/2052 Gen5;
   1060/2060/2062 Gen6), world-wide name, overall health.
2. **Firmware:** `get_versions` — bundle/firmware version **per controller**.
   Compare A and B: **mismatched firmware** between controllers is a supported
   state only mid-upgrade; a lasting mismatch is a finding.
3. **Controllers:** `get_controllers` — serials, hardware revs, redundancy mode.
4. **Enclosures & FRUs:** `get_enclosures` and `get_frus` — enclosure count,
   models, and field-replaceable units with part/serial numbers (what support
   will ask for).
5. **Licensing:** `get_license` — licensed features and any limits/expiry.
6. **Time & network:** `get_ntp_status` (clock sync — matters for correlating
   logs and for cert validity) and `get_network` (management addressing).
7. **Certificate:** `get_certificate` — HTTPS cert status/expiry on the
   management interface.

## Report

A compact inventory block: model + WWN + system name; firmware per controller
(flag any A/B mismatch); enclosure/FRU list with part+serial; licensed features;
NTP state; management IPs; cert expiry. Flag anything upgrade-relevant
(firmware drift, expiring cert, NTP not synced). Read-only — this is a record,
not a change.
