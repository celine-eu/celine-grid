# ADR-0002 — the OPA policy engine is real in tests, and the suite refuses to start without it

**Date:** 2026-08-15
**Status:** accepted

## Context

`GridAccessPolicy` fails open. When the Rego bundle will not load it returns
`Decision(True, "no-policy-engine")`; when evaluation raises it returns
`Decision(True, "policy-error-permissive")`. Both are deliberate development conveniences
and both are recorded in `docs/architecture.md`.

They also make authorisation **unobservable from a response**. A policy that ran and
permitted, and a policy that never ran at all, produce byte-identical `200`s. Nothing
distinguishes them except a log line.

That is fatal to a faked policy engine. A suite that stubbed `GridAccessPolicy` to return
allow/deny as each test wanted would assert the stub. A suite that used the real class
with the bundle missing would pass every "this is permitted" test while proving the exact
opposite of what it claimed — and would fail only its denial tests, which is precisely the
signal a reader would attribute to a fixture problem and work around.

The saving grace is that `celine.sdk.policies` evaluates Rego **in process** through
`regorus`. There is no OPA server to start, no socket, no container. The real bundle costs
about a tenth of a second to load.

## Decision

Evaluate the real `policies/grid.rego` in the test suite. Do not fake the policy engine at
any level.

Pin `CELINE_POLICIES_POLICIES_DIR` to an absolute path in `tests/conftest.py`, before the
first `celine.grid` import, because the setting's default is the relative `./policies` and
would otherwise depend on pytest's working directory.

Assert, in a session-scoped autouse fixture, that the module-level singleton actually
loaded a bundle containing `celine.grid.access`. **If it did not, the suite does not
run.** A collection error is the only failure mode that cannot be misread as a passing
authorisation test.

Test both fail-open branches explicitly, so they are a decision on the record rather than
a discovery.

## Consequences

Authorisation is the one part of this service whose tests mean what they say. Every `403`
in the suite is a real Rego denial, and every allow is a real Rego permit.

The Rego becomes the specification of record, which surfaced a disagreement immediately:
several docstrings in `src/celine/grid/api/deps.py` and the table in `docs/architecture.md` described
scope requirements the policy does not impose. `alerts.read` requires no scope at all.
Those documents were wrong and have been corrected; the tests pin the Rego.

The cost is that `policies/grid.rego` is now load-bearing for the suite. Deleting the
directory, breaking the syntax, or renaming the package stops the whole suite rather than
one file — which is the intended trade, and considerably better than the alternative,
where any of those three would leave the suite green and the service open.

`regorus` is a compiled dependency of `celine-sdk`. If a future SDK release drops it or
moves policy evaluation to an out-of-process OPA server, this decision needs revisiting
and the session fixture will say so loudly on the first run.
