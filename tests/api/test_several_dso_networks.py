"""A caller in several DSO organisations is never given the first one (REQ-0052).

A request that names a network is judged for that network. An action that names none
— `/api/me`, creating an alert rule — is refused rather than resolved to whichever DSO
organisation the token happens to list first.
"""

from __future__ import annotations

import pytest

from celine.grid.security.policy import GridAccessPolicy, _dso_network, _make_policy_input
from tests.conftest import NETWORK, OTHER_NETWORK
from tests.fakes import make_user

THIRD_NETWORK = "example-dso-c"


def _two_dsos():
    return make_user(
        sub="operator-ab",
        orgs={OTHER_NETWORK: "dso", NETWORK: "dso", "example-rec": "rec"},
        email="ab@example.test",
    )


@pytest.fixture
def two_dso_user(jwt) -> dict[str, str]:
    return jwt.headers(_two_dsos())


# @verifies REQ-0052
def test_the_network_named_is_the_one_judged():
    user = _two_dsos()
    assert _dso_network(user, NETWORK) == NETWORK
    assert _dso_network(user, OTHER_NETWORK) == OTHER_NETWORK
    # Neither named, or a network the caller is not in: no network at all.
    assert _dso_network(user) is None
    assert _dso_network(user, THIRD_NETWORK) is None
    claims = _make_policy_input(user, "alerts.write", {}).subject.claims
    assert claims == {"network_id": None, "dso_count": 2}


# @verifies REQ-0052
def test_one_dso_still_resolves_without_a_network():
    user = make_user(sub="a", orgs={NETWORK: "dso", "example-rec": "rec"})
    assert _dso_network(user) == NETWORK


# @verifies REQ-0052
@pytest.mark.parametrize("network", [NETWORK, OTHER_NETWORK])
async def test_each_named_network_reads(policy_engine_is_loaded, network):
    decision = await GridAccessPolicy().allow_network_read(_two_dsos(), network)
    assert decision.allowed


# @verifies REQ-0052
async def test_a_network_outside_the_callers_dsos_is_refused(policy_engine_is_loaded):
    decision = await GridAccessPolicy().allow_network_read(_two_dsos(), THIRD_NETWORK)
    assert not decision.allowed


# @verifies REQ-0052
async def test_writing_an_alert_rule_names_the_ambiguity(policy_engine_is_loaded):
    decision = await GridAccessPolicy().allow_alerts_write(_two_dsos())
    assert not decision.allowed
    assert decision.reason == "ambiguous DSO organization membership: name the network"


# @verifies REQ-0052
async def test_me_refuses_several_dso_organisations(client, two_dso_user):
    response = await client.get("/api/me", headers=two_dso_user)
    assert response.status_code == 403
    assert "several DSO" in response.json()["detail"]


# @verifies REQ-0052
async def test_creating_an_alert_rule_refuses_several_dso_organisations(
    client, two_dso_user
):
    response = await client.post(
        "/api/alert-rules",
        json={"risk_types": ["wind"], "threshold": "ALERT"},
        headers=two_dso_user,
    )
    assert response.status_code == 403
