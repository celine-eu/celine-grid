"""`/api/me` — the endpoint the frontend boots from.

It is where the DSO organisation becomes a `network_id`, and every grid URL the frontend
subsequently builds contains that value.
"""

from __future__ import annotations

import pytest

from tests.conftest import NETWORK, USER_SUB
from tests.fakes import make_user


# @verifies REQ-0004
async def test_the_frontend_is_told_which_network_it_is_looking_at(client, dso_user):
    """
    `network_id` and `organization` are the same string, returned twice: one is the
    path segment the frontend puts in every grid request, the other is what it prints.
    """
    response = await client.get("/api/me", headers=dso_user)

    assert response.status_code == 200
    user = response.json()["user"]
    assert user["sub"] == USER_SUB
    assert user["network_id"] == NETWORK
    assert user["organization"] == NETWORK


# @verifies REQ-0005
async def test_a_caller_with_no_dso_organisation_is_refused(client, orgless_user):
    """
    403 here means the frontend has nothing to render — there is no network to ask
    about — so this is the check that decides whether a Keycloak user can use the grid
    app at all.
    """
    assert (await client.get("/api/me", headers=orgless_user)).status_code == 403


# @verifies REQ-0044
async def test_the_optional_profile_claims_are_passed_through(client, jwt):
    """
    `locale` is the one the frontend picks a language from, and it was structurally
    dead: read with `getattr` off the `JwtUser` dataclass, which models no `locale`
    field, so the default always won however the realm mapped it. It now comes out of
    the claims. Fixed with #23.

    `name` on the line above was always fine — `JwtUser` does have that attribute — so
    this was one field, not a pattern.
    """
    headers = jwt.headers(
        make_user(
            sub="alice",
            orgs={NETWORK: "dso"},
            email="alice@example.test",
            preferred_username="alice",
            name="Alice Operator",
            locale="it",
        )
    )

    user = (await client.get("/api/me", headers=headers)).json()["user"]

    assert user["email"] == "alice@example.test"
    assert user["preferred_username"] == "alice"
    assert user["name"] == "Alice Operator"
    assert user["locale"] == "it"


# @verifies REQ-0002
async def test_a_bearer_token_reaches_the_same_answer(client, jwt):
    """
    The service-to-service header path, exercised once end to end so the middleware and
    the dependency are known to agree about it.
    """
    headers = jwt.bearer(
        make_user(sub="alice", orgs={NETWORK: "dso"}, email="alice@example.test")
    )

    assert (await client.get("/api/me", headers=headers)).status_code == 200


# @verifies REQ-0003
async def test_a_token_the_service_cannot_decode_is_a_401(client, jwt):
    """
    Past the middleware — which only checks that a token is *present* — and refused by
    the dependency.
    """
    response = await client.get(
        "/api/me", headers={"x-auth-request-access-token": "not-a-registered-token"}
    )

    assert response.status_code == 401


# @verifies REQ-0001
async def test_no_token_at_all_is_a_401(client):
    assert (await client.get("/api/me")).status_code == 401


# @verifies REQ-0043
async def test_an_operator_with_no_email_claim_can_still_boot_the_app(client, jwt):
    """
    `email` is not a mandatory Keycloak claim: a user created without one, or a realm
    that does not map it into the access token, is still a full member of the DSO.

    `MeUser.email` was required while `JwtUser.email` is optional, so such a token
    raised a `pydantic.ValidationError` **inside** the handler and escaped as a 500 with
    an empty body — and `/api/me` is the frontend's first call, so the grid app did not
    load at all for them. Fixed with #21.
    """
    headers = jwt.headers(make_user(sub="alice", orgs={NETWORK: "dso"}, email=None))

    response = await client.get("/api/me", headers=headers)

    assert response.status_code == 200
    user = response.json()["user"]
    assert user["email"] is None
    assert user["network_id"] == NETWORK


# @verifies REQ-0043
async def test_no_optional_profile_claim_is_load_bearing(client, jwt):
    """
    The general form of the same defect. Only `sub` and the DSO organisation are
    required to boot the app; every other field on `MeUser` is decoration and a token
    carrying none of them must still answer.
    """
    headers = jwt.headers(make_user(sub="alice", orgs={NETWORK: "dso"}))

    response = await client.get("/api/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["user"] == {
        "sub": "alice",
        "email": None,
        "name": None,
        "preferred_username": None,
        "locale": None,
        "network_id": NETWORK,
        "organization": NETWORK,
    }


# @verifies REQ-0039
async def test_ping_answers_for_any_authenticated_caller(client, orgless_user):
    """
    `/api/ping` is `include_in_schema=False` and needs only a decodable token — no
    organisation, no scope. It is the cheapest way to ask "is my token still good?".
    """
    response = await client.get("/api/ping", headers=orgless_user)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
