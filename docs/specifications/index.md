# Requirements

What this service must do, stated so that a test can name it.

These were **distilled from the code, not written before it** — see
`docs/decisions/ADR-0001-requirements-are-read-out-of-the-code.md`. Every one is
something `celine-grid` does today and something a reader would want to stay true; none
is an aspiration.

**All of them are satisfied.** Three were not when they were first written — writing the
sentence out is what exposed the defect in each case — and all three were fixed in the
same change as the suite that found them:

| | | |
|---|---|---|
| REQ-0043 | `/api/me` was a `500` for a DSO member with no `email` claim | [#21](https://github.com/celine-eu/celine-grid/issues/21) |
| REQ-0002 | setting `JWT_HEADER_NAME` refused every request at the middleware | [#22](https://github.com/celine-eu/celine-grid/issues/22) |
| REQ-0044 | `MeUser.locale` was structurally dead and always `null` | [#23](https://github.com/celine-eu/celine-grid/issues/23) |

## How a requirement is verified

A test declares what it covers with a `@verifies REQ-####` tag in its docstring:

```python
async def test_an_operator_may_not_read_another_dso_s_network(policy):
    """@verifies REQ-0006"""
```

The mapping is a projection of the two and is never written by hand. the harness profile
names no traceability provider, so until the harness checker is available in this
checkout the projection is a grep — `--include='*.py'` because `__pycache__` matches
otherwise:

```bash
grep -rho --include='*.py' "@verifies REQ-[0-9]\{4\}" tests/ | sort | uniq -c
```

CI checks it **both ways** on every push: a requirement no test declares is unverified,
and a tag naming a requirement that does not exist is a typo — and a typo in a trace tag
is indistinguishable from coverage until someone reads the matrix. See
`.github/workflows/test.yaml`.

Adding a requirement means adding a `REQ-####` here **and** a test declaring it, in the
same change.

## The requirements

| | |
|---|---|
| REQ-0001 – REQ-0005 | [identity](identity.md) — who the caller is |
| REQ-0006 – REQ-0013 | [authorisation](authorisation.md) — what they may do |
| REQ-0014 – REQ-0021 | [alert rules](alert-rules.md) — what an operator configures |
| REQ-0022 – REQ-0028 | [the grid proxy](grid-proxy.md) — what is forwarded to the Digital Twin |
| REQ-0029 – REQ-0038 | [alert dispatch](alert-dispatch.md) — what happens when a pipeline finishes |
| REQ-0039 – REQ-0044 | [operability](operability.md) — running, degrading, and failing |

## What is not here

- **Why** a choice was made — `docs/decisions/`.
- What the system *is* — `docs/architecture.md`.
- A trap that is true of the code and not obvious from it — the companion's knowledge.
- Anything broken — the issue tracker.
