# Alert dispatch

The half of the service with no caller. It runs on an MQTT message, writes nothing, and
reports itself only to the log — so a rule that stops firing is indistinguishable from a
calm week.

---

### REQ-0029 — only a completed run of the configured flow dispatches

The subscription is `celine/pipelines/runs/+`, which carries every pipeline in the
platform. Two filters narrow it: `status == "completed"` and `flow == GRID_PIPELINE_FLOW`
(`grid-resilience-flow` by default).

A failed run must not dispatch: the Digital Twin still holds the *previous* run's data, so
alerting on it would re-send yesterday's risk as today's.

A message that does not parse as a `PipelineRunEvent` is logged and dropped. It must never
raise out of the handler — there is no supervisor to restart the listener, and the
subscription would be lost for the life of the process.

**Dispatch is off unless `GRID_ALERTS_ENABLED=true` (default `false`).** The check comes
after the status and flow filters: a matching completed run while disabled logs one info
line (`Grid alerts disabled (GRID_ALERTS_ENABLED=false); skipping dispatch for flow=...`)
and returns without touching the Digital Twin or nudging. Every manual run or full refresh
of the grid flow emits a completed event, so without this switch each one would e-mail the
DSO; deploying a new image is therefore inert until someone enables it explicitly.

The flow name is the only filter on identity, and it is configurable, so renaming the flow
in `../celine-pipelines` silently stops all alerting here.

### REQ-0030 — the pipeline's own namespace fans out to every network with an active rule; any other namespace is a network

`celine-pipelines` publishes the grid flow under `get_namespace("grid")` — `grid`, or
`<base>.grid` when a base namespace is configured — never under a DSO alias. A completed
run whose namespace is `GRID_PIPELINE_NAMESPACE` (or ends in `.` + it) therefore evaluates
every distinct `network_id` that has an active rule, one dispatch each.

A run published under any other namespace is taken as that network alone — the original
contract, kept so a pipeline that does publish per DSO still works.

Rules backfilled by migration 002 with `network_id = ''` name no network and never
participate, in either mode.

### REQ-0031 — only active rules participate, and each distinct recipient list gets one report

Inactive rules are excluded in SQL. A network with no active rules ends the dispatch
before the Digital Twin is queried.

Triggered rules are merged per recipient list — the synthetic e-mail user, or the
operator's `sub` when there is none. Two operators watching the same network are both
told; two rules of one operator addressed to the same inbox produce one report naming
every hazard that triggered, at the lower of their thresholds. nudging-tool de-duplicates
per day on (rule, user), so sending them separately would have kept only the first.

### REQ-0032 — a threshold is a floor, and a rule watches only the hazards it names

`WARNING` triggers on kilometres at `WARNING` or `ALERT`; `ALERT` only on kilometres at
`ALERT`. An operator asking to hear about warnings certainly wants to hear about alerts.

A wind rule is not woken by a heat wave, and vice versa.

A threshold the code does not recognise falls back to the `ALERT` floor — the strict one,
which under-alerts rather than over-alerts. Only the API validator stops such a row
existing.

### REQ-0033 — a level counts only when it has kilometres

The exposure table carries `km_alert` and `km_warning` per tratta. A rule triggers when
any tratta of a hazard it names has kilometres at or above its floor; a `worst_level`
string on a row with zero kilometres does not count. A row missing its fields is survived
rather than fatal.

### REQ-0034 — the report names the hazards that triggered

`risk_types` in the report is the sorted list of hazards that met the floor for the
merged rules — `["wind"]`, `["heat"]` or both — and the report body carries one block per
named hazard. A hazard the rules watch but that is calm is not in the report.

### REQ-0035 — recipients fall back from the rule, to the settings, to the operator

A rule's own `recipients` field wins. Failing that, the operator's
`notification_settings.email_recipients`. Failing that, the nudge is addressed to the
operator's `sub` and carries no email list — the alert is not dropped for want of an
address.

A recipient list is free text: it is split on commas, semicolons or whitespace, anything
that is not an address is dropped, and duplicates are removed case-insensitively while the
first spelling and the given order are kept.

**An unparseable address is dropped silently.** An operator whose entire recipients field
is a typo is not told; the rule falls through to the next source as though the field were
empty.

### REQ-0036 — an email recipient list becomes a stable synthetic user id

`email-ingest:` followed by sixteen hex characters of SHA-256 over the sorted, lower-cased
addresses. Reordering or re-casing the field must not create a second recipient in
nudging-tool, and the same list must produce the same id on every run — nudging-tool keys
delivery preferences and de-duplication off it.

The id is not a privacy measure: the addresses travel in the same payload.

### REQ-0037 — a report covers the run's forecast horizon, or is not sent

The payload is a `grid_risk_report` carrying `period` (the run date), `dates` (the run
date and the `GRID_ALERT_HORIZON_DAYS - 1` days after it — the forecast tables hold today
plus two days), `network_id`, `threshold`, `risk_types`, `generated_at`, `app_url` and
`email_recipients`.

The run date is the date part of the event timestamp; a timestamp that cannot be read
starts the horizon today rather than cancelling it. The Digital Twin is asked once, for
the `risk_km` rows at tratta grain over exactly those dates, through the SDK's generic
values call. With no dates at all nothing is asked and nothing is sent.

### REQ-0038 — one failed send does not silence the rest

Each report is sent and caught individually, and the returned count reports what was
actually sent. One inbox's undeliverable report must not cost the rest of the network
theirs.

### REQ-0046 — the report body is the per-unit and per-line summary of the exposure table

For every date of the horizon and every hazard named, the report carries: the operational
units ordered by risk index (kilometres total, at ALERT and at WARNING, their shares, the
worst level, the three worst lines), the lines with kilometres at or above the threshold
ordered by index (at most fifteen), and the network totals. Kilometres are summed from the
tratta rows and the index is recomputed from the sums — the same arithmetic as the Digital
Twin's `line` and `unit` grains and the table page, never an average of indices — so a
number in the e-mail can be found again in the table.
