"""The 401 gate in front of every route.

`PolicyMiddleware` exists so an unauthenticated request never reaches a handler, and so
the set of routes cannot be enumerated by watching which paths 404 and which 401.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from celine.grid.security.middleware import _PUBLIC, PolicyMiddleware
from celine.grid.settings import settings


@pytest.fixture
async def bare_client():
    """The middleware alone, over one route that answers if it is reached.

    Deliberately not the real app: this file is about the gate, and a route that exists
    at every path is what makes "was the handler reached" observable.
    """
    app = FastAPI()
    app.add_middleware(PolicyMiddleware)

    @app.get("/{path:path}")
    async def anything(path: str) -> dict:
        return {"reached": path}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.parametrize("path", sorted(_PUBLIC))
# @verifies REQ-0039
async def test_the_public_paths_need_no_token(bare_client, path):
    """
    `/health` is here because a liveness probe reaches the container before any proxy
    has attached anything to it. The three OpenAPI paths are here because the schema is
    the frontend's contract — note that this publishes the full route list to anyone who
    can reach the service.
    """
    assert (await bare_client.get(path)).status_code == 200


# @verifies REQ-0001
async def test_everything_else_without_a_token_is_a_401(bare_client):
    response = await bare_client.get("/api/alert-rules")

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing authentication token"}


# @verifies REQ-0001
async def test_a_route_that_does_not_exist_is_also_a_401(bare_client):
    """
    The gate runs before routing, so an unauthenticated caller cannot tell a real path
    from an invented one. That is the property worth keeping.
    """
    assert (await bare_client.get("/api/no-such-thing")).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"x-auth-request-access-token": "anything"},
        {"authorization": "Bearer anything"},
        {"authorization": "bearer anything"},
    ],
    ids=["proxy-header", "bearer", "lowercase-bearer"],
)
# @verifies REQ-0001
async def test_the_presence_of_a_token_is_enough_to_pass_the_gate(bare_client, headers):
    """
    The middleware checks only that *something* token-shaped is present. It never
    decodes, so a garbage token passes here and is refused by the dependency — which is
    the intended split: this layer hides the routes, `api/deps.py` decides identity.
    """
    response = await bare_client.get("/api/alert-rules", headers=headers)

    assert response.status_code == 200


# @verifies REQ-0001
async def test_a_non_bearer_authorization_header_does_not_pass_the_gate(bare_client):
    assert (
        await bare_client.get(
            "/api/alert-rules", headers={"authorization": "Basic dXNlcjpwdw=="}
        )
    ).status_code == 401


# @verifies REQ-0002
async def test_the_gate_honours_jwt_header_name(bare_client, monkeypatch):
    """
    The gate and the dependency must read the *same* header name. They did not: this
    one hard-coded the default while `_extract_token` read the setting, so configuring
    `JWT_HEADER_NAME` — documented in `README.md` as supported — refused every request
    at the middleware before the dependency that honours it ever ran. Fixed with #22.
    """
    monkeypatch.setattr(settings, "jwt_header_name", "x-custom-token")

    response = await bare_client.get(
        "/api/alert-rules", headers={"x-custom-token": "a-perfectly-good-token"}
    )

    assert response.status_code == 200


# @verifies REQ-0002
async def test_the_default_header_stops_working_once_the_name_is_changed(
    bare_client, monkeypatch
):
    """
    The other half of the fix, and the reason it is not simply "accept both": the
    setting names *the* header, so once it is changed the old one is no longer an
    identity. Accepting both would make the setting additive rather than a choice, and
    would leave a deployment that moved off the default still trusting it.
    """
    monkeypatch.setattr(settings, "jwt_header_name", "x-custom-token")

    response = await bare_client.get(
        "/api/alert-rules", headers={"x-auth-request-access-token": "stale"}
    )

    assert response.status_code == 401


# @verifies REQ-0039
async def test_a_public_path_is_matched_exactly_and_not_by_prefix(bare_client):
    """
    `_PUBLIC` is a set of exact paths. `/api/docs/oauth2-redirect` — the callback
    FastAPI's Swagger UI uses for the OAuth2 flow — is not in it and answers 401.
    Harmless while the docs are browsed with a proxy session; it is the reason the
    interactive "Authorize" button cannot complete a redirect flow here.
    """
    assert (await bare_client.get("/api/docs/oauth2-redirect")).status_code == 401


# @verifies REQ-0039
async def test_the_real_app_health_check_needs_no_identity(client):
    """
    The same claim as above, made once against the real `create_app()` so the
    middleware order in `main.py` is covered and not only the middleware itself.
    """
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
