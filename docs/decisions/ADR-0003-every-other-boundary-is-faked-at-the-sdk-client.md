# ADR-0003 — every other external boundary is faked at the `celine-sdk` client

**Date:** 2026-08-15
**Status:** accepted

## Context

Besides OPA (ADR-0002), this service reaches five things it does not own: PostgreSQL, the
Digital Twin API, nudging-tool, an MQTT broker, and Keycloak's JWKS endpoint. All but the
first arrive through `celine-sdk`.

A suite that started all of them would be an integration environment. That orchestration
is the `celine-dev` workspace's to own, not this repository's, and a suite that cannot run
without it is a suite that does not run.

Two cuts were available for the four SDK-mediated dependencies:

**At the SDK client.** Replace `DTClient`, `NudgingAdminClient` and `JwtUser.from_token`
with doubles shaped like what this code reaches for. Cheap, fast, and tests exactly the
code in this repository.

**At HTTP.** Stub the transport underneath the SDK and let the real client deserialise. It
survives an SDK version bump — which matters here, because an SDK bump changes this
service's behaviour with no file in this repository changing — but it is a much larger
piece of work, and much of what it tests belongs to `celine-sdk`.

## Decision

Fake at the SDK client, in `tests/fakes.py`.

Fake as little as possible within each boundary:

- **Keycloak** — only `JwtUser.from_token` is replaced. Header ordering, the `401`
  mapping and the organisation-claim parsing stay real; `make_user` builds its
  organisations with the SDK's own `Organization._from_claim`, so the fake cannot
  disagree with the SDK about the one thing that becomes a `network_id`.
- **Digital Twin** — a `.grid` namespace that records calls and returns what a test set.
  The routes' own transformations, including the GeoJSON assembly, are real.
- **nudging-tool** — `ingest_event` only, holding the `DigitalTwinEvent` the dispatcher
  actually constructed, so a malformed payload still fails at `from_dict`.
- **MQTT** — not faked at all. `on_pipeline_run` is called directly with a message
  object; the broker is never constructed.
- **PostgreSQL** — not faked. See ADR-0004.

## Consequences

`task test` needs no service, no network and no container, and takes about eleven seconds.

**A green run says nothing about whether `celine-sdk` still returns these shapes.** The
fakes describe what this repository *assumes*, written from this repository. If the SDK
renames a field or returns models where it returned `to_dict()`-able rows, every test
still passes and the affected call fails in production. This is the stated cost, not an
oversight — the companion's knowledge says what to do after an SDK
bump, and `test_grid_proxy.py` pins the one place a shape change surfaces as an unhandled
`500` rather than a handled error.

The cheapest thing that would close it: build the fakes' return values from the SDK's own
response classes rather than from plain dicts. That is real work and nothing owes it yet.

The MQTT decision has a smaller cost of the same kind: nothing in the suite exercises
`create_broker()`, the subscription topic, or the startup timeout. Those are covered by
starting the service, and by nothing else.
