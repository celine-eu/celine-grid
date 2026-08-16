"""Where a token is looked for, and what a bad one becomes.

Only the signature check is faked (`FakeJwt`). The header ordering, the 401 mapping and
the DSO resolution are the real code.
"""

from __future__ import annotations

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

from celine.grid.api import deps as deps_module
from celine.grid.api.deps import (
    _extract_token,
    get_client_ip,
    get_dt_client,
    get_user_from_request,
    resolve_dso_network,
)
from celine.grid.settings import settings
from tests.fakes import FakeJwt, make_user

NETWORK = "example-dso"


def request_with(headers: dict[str, str], *, client: tuple[str, int] | None = None):
    """A Starlette `Request` carrying *headers* and nothing else."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/me",
        "headers": Headers(headers).raw,
        "query_string": b"",
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


# ---------------------------------------------------------------------------
# Where the token comes from
# ---------------------------------------------------------------------------


# @verifies REQ-0002
def test_the_proxy_header_is_read_first():
    """
    `x-auth-request-access-token` is what OAuth2 Proxy attaches. It wins over an
    `Authorization` header, so a stale bearer token a caller kept cannot override the
    identity the proxy established.
    """
    token = _extract_token(
        request_with(
            {
                "x-auth-request-access-token": "from-proxy",
                "authorization": "Bearer from-caller",
            }
        )
    )

    assert token == "from-proxy"


# @verifies REQ-0002
def test_a_bearer_header_is_accepted_when_the_proxy_header_is_absent():
    """
    This is the service-to-service path: nothing proxies a client-credentials call.
    """
    assert _extract_token(request_with({"authorization": "Bearer abc"})) == "abc"


# @verifies REQ-0002
def test_the_bearer_scheme_is_matched_case_insensitively():
    assert _extract_token(request_with({"authorization": "bearer abc"})) == "abc"
    assert _extract_token(request_with({"authorization": "BEARER abc"})) == "abc"


# @verifies REQ-0002
def test_a_non_bearer_authorization_header_yields_no_token():
    """
    Basic auth is ignored rather than rejected here; the 401 is raised by the caller.
    """
    assert _extract_token(request_with({"authorization": "Basic dXNlcjpwdw=="})) is None


# @verifies REQ-0002
def test_no_headers_at_all_yields_no_token():
    assert _extract_token(request_with({})) is None


# @verifies REQ-0002
def test_the_proxy_header_name_is_configurable(monkeypatch):
    """
    `JWT_HEADER_NAME` is documented as configurable and `_extract_token` honours it.
    `PolicyMiddleware` does **not** — see `test_middleware.py`, where the consequence
    of that split is pinned.
    """
    monkeypatch.setattr(settings, "jwt_header_name", "x-custom-token")

    assert _extract_token(request_with({"x-custom-token": "abc"})) == "abc"


# ---------------------------------------------------------------------------
# What a bad token becomes
# ---------------------------------------------------------------------------


# @verifies REQ-0001
def test_a_request_with_no_token_is_a_401():
    with pytest.raises(HTTPException) as exc:
        get_user_from_request(request_with({}))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Missing authentication token"


@pytest.mark.parametrize(
    ("raised", "detail_starts"),
    [
        (pyjwt.ExpiredSignatureError("expired"), "Token has expired"),
        (pyjwt.InvalidTokenError("bad signature"), "Invalid token"),
        (ConnectionError("JWKS unreachable"), "Authentication failed"),
    ],
    ids=["expired", "invalid", "jwks-unreachable"],
)
# @verifies REQ-0003
def test_every_decode_failure_is_a_401(monkeypatch, raised, detail_starts):
    """
    Including the third one: when Keycloak's JWKS endpoint is unreachable the service
    answers `401`, not `503`. A caller cannot distinguish "your token is forged" from
    "our identity provider is down", which is worth knowing before diagnosing a
    site-wide wave of 401s as an attack.
    """
    fake = FakeJwt()
    fake.raises = raised
    monkeypatch.setattr(deps_module, "JwtUser", fake)

    with pytest.raises(HTTPException) as exc:
        get_user_from_request(request_with({"authorization": "Bearer whatever"}))

    assert exc.value.status_code == 401
    assert exc.value.detail.startswith(detail_starts)


# ---------------------------------------------------------------------------
# The network identity
# ---------------------------------------------------------------------------


# @verifies REQ-0004
def test_the_dso_alias_is_the_network_id():
    """
    No mapping table exists. Renaming an organisation in Keycloak changes which network
    this service asks the Digital Twin about, with no change in this repository — see
    `.agents/knowledge/what-this-repository-depends-on.md`.
    """
    user = make_user(sub="alice", orgs={NETWORK: "dso"})

    assert resolve_dso_network(user) == NETWORK


# @verifies REQ-0004
def test_a_dso_organisation_is_picked_out_from_among_others():
    user = make_user(
        sub="alice", orgs={"a-cooperative": "rec", NETWORK: "dso", "a-city": None}
    )

    assert resolve_dso_network(user) == NETWORK


# @verifies REQ-0004
def test_the_attributes_map_is_checked_as_well_as_the_top_level_type():
    user = make_user(
        sub="alice", orgs={NETWORK: None}, org_attributes={NETWORK: {"type": ["dso"]}}
    )

    assert resolve_dso_network(user) == NETWORK


# @verifies REQ-0005
def test_no_dso_membership_is_a_403():
    """
    A 403 rather than a 401: the caller is authenticated, they are simply not a grid
    operator.
    """
    with pytest.raises(HTTPException) as exc:
        resolve_dso_network(make_user(sub="alice", orgs={"a-cooperative": "rec"}))

    assert exc.value.status_code == 403
    assert exc.value.detail == "DSO organisation membership required"


# @verifies REQ-0004
def test_which_of_several_dso_organisations_wins_is_claim_order():
    """
    A caller belonging to two DSOs gets the first one in the `organization` claim, and
    nothing anywhere states which that is. Pinned as observed rather than asserted as
    correct: if multi-DSO membership ever becomes real, this test is where the
    ambiguity surfaces. See `.agents/knowledge/one-operator-one-network.md`.
    """
    user = make_user(sub="alice", orgs={"first-dso": "dso", "second-dso": "dso"})

    assert resolve_dso_network(user) == "first-dso"


# ---------------------------------------------------------------------------
# Upstream client construction
# ---------------------------------------------------------------------------


# @verifies REQ-0022
def test_the_grid_endpoints_refuse_loudly_when_the_digital_twin_is_unconfigured(
    monkeypatch,
):
    """
    503, not an empty result. The grid endpoints are the one dependency in this service
    that fails loudly rather than degrading — worth keeping, because a silent empty map
    reads as "no risk today".
    """
    monkeypatch.setattr(settings, "digital_twin_api_url", None)

    with pytest.raises(HTTPException) as exc:
        get_dt_client(request_with({}))

    assert exc.value.status_code == 503
    assert exc.value.detail == "Digital Twin API not configured"


# ---------------------------------------------------------------------------
# Client address, used only in logs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "client", "expected"),
    [
        ({"x-forwarded-for": "203.0.113.9, 10.0.0.1"}, None, "203.0.113.9"),
        ({"x-real-ip": "203.0.113.9"}, None, "203.0.113.9"),
        ({}, ("10.0.0.5", 1234), "10.0.0.5"),
        ({}, None, "unknown"),
    ],
    ids=["forwarded-for", "real-ip", "socket", "nothing"],
)
# @verifies REQ-0042
def test_the_client_address_falls_back_through_three_sources(headers, client, expected):
    """
    `X-Forwarded-For` is caller-controlled and this value is only ever logged. It must
    not grow into an authorisation input.
    """
    assert get_client_ip(request_with(headers, client=client)) == expected
