"""Real Keycloak tokens through the real app: the realm level grants nothing here.

The rest of the suite fakes `JwtUser.from_token`. These tests do not: they mint access
tokens from a running Keycloak, and send them through the real middleware, the real
signature and audience check (`settings.oidc`), and the real `policies/grid.rego`. Only
the Digital Twin and the database are faked, as everywhere else.

**Opt-in.** They run only when `CELINE_GRID_IT_KEYCLOAK` names the realm issuer, and that
issuer must be the one this service verifies against (`CELINE_OIDC_BASE_URL`, local by
default). Point it at a local development realm, never at a shared one:

```bash
CELINE_GRID_IT_KEYCLOAK=http://keycloak.celine.localhost/realms/celine uv run pytest tests/integration
```

The realm must follow the two-level model: the realm role `platform-admin` held by the
user `CELINE_GRID_IT_PLATFORM_ADMIN` (default `admin`), an organisation admin who is not a
platform admin in `CELINE_GRID_IT_ORG_ADMIN` (default `org-admin`), passwords equal to the
usernames (the development realm's convention), and a user-token client
`CELINE_GRID_IT_CLIENT_ID` / `_SECRET` (default `oauth2_proxy` / `oauth2_proxy`).

`CELINE_GRID_IT_LEGACY_TOKEN` optionally carries a token minted while a realm group
(`/admins`) still reached the `groups` claim. Such a token cannot be minted from a
converged realm, so it is supplied rather than fetched; without it that test is skipped.
"""

from __future__ import annotations

import os

import httpx
import pytest
from celine.sdk.auth import JwtUser, organization_groups

from celine.grid.security.policy import GridAccessPolicy, _dso_network, _make_policy_input
from celine.grid.settings import settings

ISSUER = os.getenv("CELINE_GRID_IT_KEYCLOAK", "").rstrip("/")
CLIENT_ID = os.getenv("CELINE_GRID_IT_CLIENT_ID", "oauth2_proxy")
CLIENT_SECRET = os.getenv("CELINE_GRID_IT_CLIENT_SECRET", "oauth2_proxy")
PLATFORM_ADMIN = os.getenv("CELINE_GRID_IT_PLATFORM_ADMIN", "admin")
ORG_ADMIN = os.getenv("CELINE_GRID_IT_ORG_ADMIN", "org-admin")
LEGACY_TOKEN = os.getenv("CELINE_GRID_IT_LEGACY_TOKEN", "").strip()

# A network no development user belongs to.
NOBODY_S_NETWORK = "not-a-member-dso"

pytestmark = [
    pytest.mark.skipif(not ISSUER, reason="CELINE_GRID_IT_KEYCLOAK is not set"),
    pytest.mark.skipif(
        bool(ISSUER) and ISSUER != settings.oidc.base_url.rstrip("/"),
        reason="CELINE_GRID_IT_KEYCLOAK differs from the issuer this service verifies "
        f"against ({settings.oidc.base_url})",
    ),
]


def _mint(username: str) -> str:
    response = httpx.post(
        f"{ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username": username,
            "password": username,
            # what oauth2-proxy requests; plain `organization` lists no organisation
            # for a user who belongs to several
            "scope": "openid email profile organization:*",
        },
        timeout=10,
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _decode(token: str) -> JwtUser:
    """The same verified decode `get_user_from_request` performs."""
    return JwtUser.from_token(token, oidc=settings.oidc)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_only_organisation_groups(user: JwtUser) -> None:
    """For every network a request could name, the policy sees that organisation's groups."""
    for alias in [*user.organization_aliases, NOBODY_S_NETWORK]:
        subject = _make_policy_input(user, "read", {"network_id": alias}).subject
        assert subject.groups == organization_groups(user.claims, alias), alias
        assert "platform-admin" not in subject.groups


@pytest.fixture
def policy(policy_engine_is_loaded) -> GridAccessPolicy:
    p = GridAccessPolicy()
    assert p._engine is not None
    return p


# @verifies REQ-0051
async def test_a_real_organisation_admin_is_not_a_platform_admin(client, policy):
    token = _mint(ORG_ADMIN)
    user = _decode(token)

    assert any(
        "admins" in organization_groups(user.claims, alias)
        for alias in user.organization_aliases
    ), "the organisation-admin fixture user holds no organisation's admins"
    assert not user.is_platform_admin
    _assert_only_organisation_groups(user)

    response = await client.get(f"/api/grid/{NOBODY_S_NETWORK}/filters", headers=_bearer(token))
    assert response.status_code == 403, response.text
    if _dso_network(user) is None:
        assert not (await policy.allow_alerts_write(user)).allowed


# @verifies REQ-0051
async def test_a_real_platform_admin_reaches_only_the_networks_it_is_a_member_of(client):
    token = _mint(PLATFORM_ADMIN)
    user = _decode(token)

    assert user.is_platform_admin
    _assert_only_organisation_groups(user)

    response = await client.get(f"/api/grid/{NOBODY_S_NETWORK}/filters", headers=_bearer(token))
    assert response.status_code == 403, response.text

    own = _dso_network(user)
    if own is not None:
        response = await client.get(f"/api/grid/{own}/filters", headers=_bearer(token))
        assert response.status_code == 200, response.text


# @verifies REQ-0051
@pytest.mark.skipif(not LEGACY_TOKEN, reason="CELINE_GRID_IT_LEGACY_TOKEN is not set")
async def test_a_real_token_still_carrying_a_realm_group_grants_nothing(client, policy):
    user = _decode(LEGACY_TOKEN)

    realm_groups = user.claims.get("groups") or []
    assert realm_groups, "the legacy token carries no realm `groups` claim"
    assert not user.is_platform_admin
    _assert_only_organisation_groups(user)

    response = await client.get(
        f"/api/grid/{NOBODY_S_NETWORK}/filters", headers=_bearer(LEGACY_TOKEN)
    )
    assert response.status_code == 403, response.text
    if _dso_network(user) is None:
        assert not (await policy.allow_alerts_write(user)).allowed
        for alias in user.organization_aliases:
            response = await client.get(f"/api/grid/{alias}/filters", headers=_bearer(LEGACY_TOKEN))
            assert response.status_code == 403, response.text
