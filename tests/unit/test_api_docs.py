"""The interactive API docs and the schema are mounted only in development.

`celine.sdk.posture.docs_urls`: in `CELINE_ENV=dev` the three paths are served; anywhere
else — unset included — they are not mounted unless `CELINE_PUBLIC_DOCS=true`.
They stay in `_PUBLIC`, so an unmounted path is a plain `404`, not a `401`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from celine.grid.main import create_app

PATHS = ("/api/docs", "/api/redoc", "/api/openapi.json")


def _client(monkeypatch, env: str | None, public: str | None = None) -> TestClient:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    if env is None:
        monkeypatch.delenv("CELINE_ENV", raising=False)
    else:
        monkeypatch.setenv("CELINE_ENV", env)
    if public is None:
        monkeypatch.delenv("CELINE_PUBLIC_DOCS", raising=False)
    else:
        monkeypatch.setenv("CELINE_PUBLIC_DOCS", public)
    # No `with`: the lifespan (posture guard, database, MQTT) is not under test here.
    return TestClient(create_app())


@pytest.mark.parametrize("env", [None, "staging", "prod"])
# @verifies REQ-0039
def test_outside_dev_the_docs_are_not_mounted(monkeypatch, env):
    client = _client(monkeypatch, env)

    assert [client.get(p).status_code for p in PATHS] == [404, 404, 404]


@pytest.mark.parametrize("public", ["", "false", "0"])
# @verifies REQ-0039
def test_only_a_true_opt_in_serves_them_outside_dev(monkeypatch, public):
    client = _client(monkeypatch, "staging", public)

    assert [client.get(p).status_code for p in PATHS] == [404, 404, 404]


# @verifies REQ-0039
def test_the_public_docs_opt_in_serves_them_outside_dev(monkeypatch):
    client = _client(monkeypatch, "staging", "true")

    assert [client.get(p).status_code for p in PATHS] == [200, 200, 200]


# @verifies REQ-0039
def test_dev_serves_the_docs(monkeypatch):
    client = _client(monkeypatch, "dev")

    assert [client.get(p).status_code for p in PATHS] == [200, 200, 200]
    assert client.get("/api/openapi.json").json()["info"]["title"] == "CELINE Grid BFF"
