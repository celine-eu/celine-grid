# ADR-0004 — the database is real in tests, and it is SQLite

**Date:** 2026-08-15
**Status:** accepted

## Context

The confidentiality of alert rules rests on SQL, not on the policy: `alerts.read` is
permitted for every authenticated non-service caller (REQ-0010), so `WHERE user_id = :sub`
is the whole of the separation between two operators. Dispatch scoping is SQL too —
`WHERE network_id = :network AND active IS TRUE`.

A faked store would assert the fake's filtering. Whatever else is faked here, this cannot
be.

That leaves how to run the real SQL. PostgreSQL is production and the only dialect the
migrations target; requiring it would mean no test runs without a container.

## Decision

Run the real models and the real queries against in-memory SQLite via `aiosqlite`, with
the schema built from `Base.metadata.create_all` — not from the migrations.

One engine per test, on `StaticPool` so every connection reaches the same in-memory
database.

`DATABASE_URL` stays a PostgreSQL DSN pointing at a closed port. It is parsed at import to
build the module-level engines and never connected to; the tests get their session by
overriding the `get_db` dependency. It must stay a `postgresql+asyncpg://` URL:
`src/celine/grid/db/session.py` derives the Alembic sync engine by stripping `+asyncpg` from it, and
`sqlite+aiosqlite` survives that strip unchanged, at which point `create_engine()` raises
on an async driver and the whole suite fails to collect.

## Consequences

Every isolation test in the suite runs the SQL that ships.

Three things it does not prove, all of which need PostgreSQL:

- **That the migrations produce this schema.** The tests build tables from the models;
  `alembic upgrade head` is a separate path and is exercised by nothing. A model that
  drifts from its migrations passes the whole suite. `uv run alembic upgrade head`
  followed by `uv run alembic check` is the missing step, and it needs a database.
- **Ordering of ties.** `ORDER BY created_at` with second granularity ties often, and the
  two dialects need not break a tie the same way. REQ-0015 states the tie rather than
  asserting a winner, for that reason.
- **Type behaviour.** `Uuid` is a native type on PostgreSQL and `CHAR(32)` on SQLite;
  `JSON` is `JSONB`-adjacent on one and text on the other. Nothing here depends on the
  difference today.

`../celine-ai-assistant` closes the first two with a CI job that re-runs its database
tests against a real PostgreSQL service container (its ADR-0007). Doing the same here is
the obvious next step and is recorded in the companion's plans as owed, not
done.
