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

The flow name is the only filter on identity, and it is configurable, so renaming the flow
in `../celine-pipelines` silently stops all alerting here.

### REQ-0030 — the run's namespace is the network

`PipelineRunEvent.namespace` is used directly as the `network_id`. It is the third place
the same unmapped identifier appears — Keycloak organisation alias, Digital Twin
`network_id`, Prefect namespace — with no translation between any of them.

Only rules carrying that `network_id` are loaded. Rules backfilled by migration 002 with
`network_id = ''` match no real network and are therefore inert.

### REQ-0031 — only active rules participate, and each gets its own nudge

Inactive rules are excluded in SQL. A network with no active rules ends the dispatch
before anything else happens. Every rule that triggers produces one nudge, so two
operators watching the same network are both told.

### REQ-0032 — a threshold is a floor, and a rule watches only the hazards it names

`WARNING` triggers on `WARNING` or `ALERT`; `ALERT` triggers only on `ALERT`. An operator
asking to hear about warnings certainly wants to hear about alerts.

A wind rule is not woken by a heat wave, and vice versa.

A threshold the code does not recognise falls back to the `ALERT` floor — the strict one,
which under-alerts rather than over-alerts. Only the API validator stops such a row
existing.

### REQ-0033 — a risk level counts only when it has events

The Digital Twin returns a row per level, including levels with `events: 0`. Testing for
the row rather than the count would fire every rule on every run.

Levels are compared case-insensitively, and a row missing its fields is survived rather
than fatal.

### REQ-0034 — the hazard reported is the one that triggered

A wind rule that fires reports `wind`, a heat rule `heat`, and a rule watching both that
triggers on both reports `thunderstorm` — nudging-tool's vocabulary for a combined event —
as a single nudge, not two.

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

### REQ-0037 — a nudge carries the pipeline's window, or is not sent

The payload is an `extr_event` — not `grid_alert` — carrying `period`, `window_start` and
`window_end`, which nudging-tool renders into the message an operator reads.

`period` is a date, taken from the payload or derived from the event timestamp.
`window_start` and `window_end` are zero-padded `HH:MM`, taken from the payload only. All
three are searched for at the top level of the payload and then inside `facts`, `payload`,
`metadata`, `parameters`, `params` and `data`, because different pipelines put them in
different places.

**If any of the three is missing, the entire dispatch is cancelled** before the Digital
Twin is queried. A nudge whose window reads `None` is worse than no nudge. A pipeline
emitting `6:00` rather than `06:00` fails this and stops alerting silently.

### REQ-0038 — one failed send does not silence the rest

Each nudge is sent and caught individually, and the returned count reports what was
actually sent. One operator's undeliverable alert must not cost the rest of the network
theirs.
