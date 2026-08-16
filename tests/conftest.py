"""Shared fixtures.

**No test reaches a real service — with one deliberate exception.** This repository
talks to PostgreSQL, the Digital Twin API, nudging-tool, an MQTT broker and Keycloak;
all five are faked here at the narrowest boundary that still exercises our code.

OPA is the exception, and it is on purpose. `celine.sdk.policies` evaluates Rego
**in process** via `regorus` — no server, no socket — so the real `policies/grid.rego`
is what the suite evaluates. Faking it would be worse than useless: `GridAccessPolicy`
falls back to `allow=True` when the bundle will not load, so a suite that faked the
engine and a suite whose engine silently failed to load would produce identical passes.
See ADR-0002 and `.agents/knowledge/the-policy-engine-fails-open.md`.

The environment is set *before* `celine.grid` is imported anywhere. `settings.py` builds
its `Settings()` at import time, `db/session.py` builds both engines from it, and
`security/policy.py` loads the Rego bundle into a module-level singleton — by the time a
test module is collected the wiring has already happened and cannot be undone by a
fixture. See `.agents/knowledge/import-time-wiring.md`.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Must run before the first `celine.grid` import. Do not move below them.
# ---------------------------------------------------------------------------

# Parsed by SQLAlchemy at import to build both engines; never connected to, because
# every test that needs a database gets the SQLite session from the `db` fixture.
#
# It stays a *postgres* DSN on purpose. `db/session.py` derives the Alembic sync engine
# by stripping `+asyncpg` from this string, and `sqlite+aiosqlite` survives that strip
# unchanged — `create_engine()` then raises on an async driver at import time and the
# whole suite fails to collect.
os.environ["DATABASE_URL"] = "postgresql+asyncpg://test:test@127.0.0.1:1/test"
os.environ["CELINE_MQTT_HOST"] = "127.0.0.1"
os.environ["CELINE_MQTT_PORT"] = "1"

# `policies_dir` defaults to the relative `./policies`, so the bundle only loads when
# pytest runs from the repository root. Pin it to the real directory by absolute path so
# the suite is independent of the working directory — and so a missing bundle is a
# collection error rather than a suite that silently allows everything.
_REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ["CELINE_POLICIES_POLICIES_DIR"] = str(_REPO_ROOT / "policies")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from celine.grid.api import deps as deps_module  # noqa: E402
from celine.grid.db.models import Base  # noqa: E402
from celine.grid.db.session import get_db  # noqa: E402
from celine.grid.main import create_app  # noqa: E402
from celine.grid.security import policy as policy_module  # noqa: E402

from tests.fakes import FakeDTClient, FakeJwt, make_user  # noqa: E402

NETWORK = "example-dso"
OTHER_NETWORK = "other-dso"
USER_SUB = "user-alice"
OTHER_SUB = "user-bob"


# ---------------------------------------------------------------------------
# The policy engine must be real, and must be loaded
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def policy_engine_is_loaded():
    """Refuse to run if the Rego bundle did not load.

    `GridAccessPolicy._evaluate` returns `Decision(True, "no-policy-engine")` when
    `self._engine is None`. Every `403` assertion in this suite would then pass for the
    wrong reason — or rather, would fail, but every *allow* assertion would pass while
    proving nothing at all. This fixture is the difference between "the policy permits
    it" and "no policy ran".
    """
    engine = policy_module.policy._engine
    assert engine is not None, (
        "the OPA bundle did not load, so GridAccessPolicy is in its permissive "
        "fallback and no authorisation assertion in this suite means anything"
    )
    assert engine.has_package("celine.grid.access")
    return engine


# ---------------------------------------------------------------------------
# Database — real SQLAlchemy, real SQL, SQLite instead of PostgreSQL
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_sessionmaker():
    """A SQLite engine carrying the real `Base.metadata` schema.

    One engine per test, so a test never sees another test's rows. `StaticPool` is what
    makes an in-memory SQLite database usable at all here: the default pool hands each
    connection its own private database, which would lose the tables between
    `create_all` and the first query.

    This is not PostgreSQL, and the schema is built from the models rather than from the
    migrations. What that costs is stated in
    `docs/decisions/ADR-0004-the-database-is-real-and-sqlite.md`.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    await engine.dispose()


@pytest.fixture
async def db(db_sessionmaker) -> AsyncSession:
    """A session for arranging rows and asserting on them directly."""
    async with db_sessionmaker() as session:
        yield session


# ---------------------------------------------------------------------------
# Identity — the JWKS fetch and the signature check are faked, nothing else is
# ---------------------------------------------------------------------------


@pytest.fixture
def jwt(monkeypatch) -> FakeJwt:
    """Mint tokens that `get_user_from_request` will accept.

    Only `JwtUser.from_token` is replaced — signing a real RS256 token and serving a
    JWKS would be testing PyJWT. Header extraction, the 401 mapping and the
    organisation parsing all stay real: `make_user` builds its `Organization` list with
    the SDK's own `Organization._from_claim`.
    """
    fake = FakeJwt()
    monkeypatch.setattr(deps_module, "JwtUser", fake)
    return fake


@pytest.fixture
def dso_user(jwt: FakeJwt) -> dict[str, str]:
    """Headers for a DSO operator belonging to `example-dso`.

    Carries an `email` claim because a real operator's token does. Nothing *requires*
    one — `test_me.py` covers the token that has none, which was a 500 until #21 — so
    this fixture is realism, not a workaround.
    """
    return jwt.headers(
        make_user(sub=USER_SUB, orgs={NETWORK: "dso"}, email="alice@example.test")
    )


@pytest.fixture
def other_dso_user(jwt: FakeJwt) -> dict[str, str]:
    """Headers for an operator of a *different* DSO."""
    return jwt.headers(
        make_user(sub=OTHER_SUB, orgs={OTHER_NETWORK: "dso"}, email="bob@example.test")
    )


@pytest.fixture
def orgless_user(jwt: FakeJwt) -> dict[str, str]:
    """Headers for an authenticated user belonging to no organisation at all."""
    return jwt.headers(
        make_user(sub="user-nobody", orgs={}, email="nobody@example.test")
    )


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


@pytest.fixture
def dt() -> FakeDTClient:
    return FakeDTClient()


@pytest.fixture
def app(db_sessionmaker, dt: FakeDTClient):
    """The real `create_app()`, with the database and the DT client overridden.

    The lifespan connects to MQTT and calls `init_db()`; httpx's `ASGITransport` never
    emits lifespan events, which is what keeps both from running. `PolicyMiddleware`,
    the routers and every `Depends` chain are the real ones.
    """
    application = create_app()

    async def _get_db():
        async with db_sessionmaker() as session:
            yield session

    application.dependency_overrides[get_db] = _get_db
    application.dependency_overrides[deps_module.get_dt_client] = lambda: dt
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
