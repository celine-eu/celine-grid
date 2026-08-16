"""The authorisation rules, evaluated against the real `policies/grid.rego`.

These are the tests the suite exists for. `GridAccessPolicy` returns
`Decision(True, ...)` whenever the engine is missing or the evaluation raises, so an
*allow* proves nothing on its own — a denial is the only observation that distinguishes
a policy that ran from one that was never consulted. The session-wide
`policy_engine_is_loaded` fixture in `conftest.py` refuses to run the suite otherwise.

Where a docstring in `api/deps.py` and `policies/grid.rego` disagree about what an
action requires, **the Rego is what runs** and these tests pin the Rego. The
disagreements are recorded in `.agents/knowledge/what-the-policy-actually-requires.md`.
"""

from __future__ import annotations

import pytest
from celine.sdk.policies import SubjectType

from celine.grid.security.policy import GridAccessPolicy, _make_policy_input
from tests.fakes import make_service, make_user

NETWORK = "example-dso"


@pytest.fixture
def policy(policy_engine_is_loaded) -> GridAccessPolicy:
    """A freshly built policy over the real bundle.

    Built rather than imported so a test cannot be affected by whatever the module-level
    singleton picked up; the loaded-engine assertion is inherited from the session
    fixture, which has already refused to let the suite start without one.
    """
    p = GridAccessPolicy()
    assert p._engine is not None
    return p


# ---------------------------------------------------------------------------
# What types a subject
# ---------------------------------------------------------------------------


# @verifies REQ-0009
def test_a_member_of_an_organisation_is_a_user_even_when_it_looks_like_a_service():
    """
    `is_service_account()` reads `preferred_username`, and a user token that happens to
    carry a `scope` claim but no `groups` can trip it. Organisation presence is the
    signal `_make_policy_input` trusts first, which is what keeps a DSO operator from
    being evaluated under the service rules — where `owns_network` is never checked.
    """
    user = make_user(
        sub="alice",
        orgs={NETWORK: "dso"},
        scope="grid.read",
        preferred_username="service-account-looks-wrong",
    )

    subject = _make_policy_input(user, "read", {}).subject

    assert subject.type is SubjectType.USER
    assert subject.claims["network_id"] == NETWORK


# @verifies REQ-0009
def test_a_principal_with_no_organisation_and_a_service_username_is_a_service():
    subject = _make_policy_input(make_service(scope="grid.read"), "read", {}).subject

    assert subject.type is SubjectType.SERVICE
    # A service has no DSO org, and the ownership check must not be given one to match.
    assert subject.claims["network_id"] is None


# @verifies REQ-0009
def test_an_authenticated_stranger_is_a_user_not_a_service():
    """
    No organisation, no service-account username: the fallback is USER, which is the
    conservative branch — it is the one where `read` requires `owns_network`.
    """
    subject = _make_policy_input(
        make_user(sub="nobody", orgs={}, email="nobody@example.test"), "read", {}
    ).subject

    assert subject.type is SubjectType.USER
    assert subject.claims["network_id"] is None


# @verifies REQ-0004
def test_the_dso_organisation_is_found_in_the_nested_attributes_map_too():
    """
    KC 26's org mapper emits `type` at the top of the org dict with
    `org.add.attributes` off, and inside `attributes` with it on. Both are in
    production, so both must resolve to the same network.
    """
    user = make_user(
        sub="alice",
        orgs={NETWORK: None},
        org_attributes={NETWORK: {"type": ["dso"]}},
    )

    assert _make_policy_input(user, "read", {}).subject.claims["network_id"] == NETWORK


# @verifies REQ-0004
def test_a_non_dso_organisation_is_not_a_network():
    """
    Membership of *an* organisation is not membership of a DSO. Only `type=dso` yields
    a network, because the alias is used unmapped as the DT `network_id`.
    """
    user = make_user(sub="alice", orgs={"some-cooperative": "rec"})

    assert _make_policy_input(user, "read", {}).subject.claims["network_id"] is None


# ---------------------------------------------------------------------------
# Grid data — read
# ---------------------------------------------------------------------------


# @verifies REQ-0006
async def test_an_operator_may_read_their_own_network(policy):
    user = make_user(sub="alice", orgs={NETWORK: "dso"})

    decision = await policy.allow_network_read(user, NETWORK)

    assert decision.allowed
    assert decision.reason == "user accessing own network data"


# @verifies REQ-0006
async def test_an_operator_may_not_read_another_dso_s_network(policy):
    """
    The denial that matters most in this repository. A wrong `network_id` does not
    error at the Digital Twin — it returns another network's valid-looking data — so
    this rule is the only thing between an operator and someone else's grid.
    """
    user = make_user(sub="alice", orgs={NETWORK: "dso"})

    decision = await policy.allow_network_read(user, "other-dso")

    assert not decision.allowed
    assert decision.reason == (
        "network_id mismatch: user does not belong to the requested DSO"
    )


# @verifies REQ-0005
async def test_a_user_with_no_dso_organisation_may_read_no_network(policy):
    user = make_user(sub="nobody", orgs={}, email="nobody@example.test")

    decision = await policy.allow_network_read(user, NETWORK)

    assert not decision.allowed


# @verifies REQ-0006
async def test_holding_a_grid_scope_does_not_let_a_user_read_another_network(policy):
    """
    Scopes are the *service* branch of the policy. A user token carrying `grid.admin`
    is still checked for ownership, so a scope leaked into a user's client cannot be
    used to widen which network they see.
    """
    user = make_user(sub="alice", orgs={NETWORK: "dso"}, scope="grid.admin grid.read")

    assert not (await policy.allow_network_read(user, "other-dso")).allowed


# @verifies REQ-0007
async def test_a_service_with_grid_read_may_read_any_network(policy):
    """
    Service accounts carry no organisation, so there is nothing to own a network with;
    scope is how a service expresses intent instead.
    """
    decision = await policy.allow_network_read(make_service(scope="grid.read"), NETWORK)

    assert decision.allowed
    assert decision.reason == "service access granted"


# @verifies REQ-0007
async def test_grid_admin_also_reads(policy):
    assert (
        await policy.allow_network_read(make_service(scope="grid.admin"), NETWORK)
    ).allowed


# @verifies REQ-0008
async def test_a_service_with_no_grid_scope_is_refused(policy):
    decision = await policy.allow_network_read(
        make_service(scope="openid profile"), NETWORK
    )

    assert not decision.allowed
    assert decision.reason == "service missing grid.read scope"


# @verifies REQ-0008
async def test_a_service_with_no_scope_claim_at_all_is_refused(policy):
    """
    `_make_policy_input` reads `scope` as a space-separated string and tolerates its
    absence. Absent must land on the same denial as present-and-wrong.
    """
    assert not (await policy.allow_network_read(make_service(), NETWORK)).allowed


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------


# @verifies REQ-0010
async def test_any_authenticated_user_may_read_alert_rules(policy):
    """
    No scope and no organisation are required — the Rego allows `alerts.read` for every
    non-service subject. This is *not* what `require_alerts_read`'s docstring says, and
    the Rego is what runs. Confidentiality here rests entirely on the
    `WHERE user_id = :sub` in `api/alerts.py`, which `tests/api/test_alert_rules.py`
    covers. See `.agents/knowledge/what-the-policy-actually-requires.md`.
    """
    stranger = make_user(sub="nobody", orgs={}, email="nobody@example.test")

    assert (await policy.allow_alerts_read(stranger)).allowed


# @verifies REQ-0011
async def test_writing_an_alert_rule_requires_a_dso_organisation(policy):
    """
    A rule carries a `network_id`, and the only place one comes from is the caller's
    DSO organisation. Without it there is nothing to write.
    """
    decision = await policy.allow_alerts_write(
        make_user(sub="nobody", orgs={}, email="nobody@example.test")
    )

    assert not decision.allowed
    assert decision.reason == "missing DSO organization membership"


# @verifies REQ-0011
async def test_a_dso_operator_may_write_alert_rules_without_any_scope(policy):
    """
    Also not what the docstring says: no `grid.alerts.write` scope is consulted.
    Organisation membership is the whole check.
    """
    user = make_user(sub="alice", orgs={NETWORK: "dso"})

    decision = await policy.allow_alerts_write(user)

    assert decision.allowed
    assert decision.reason == "user managing own alert rules"


# @verifies REQ-0012
async def test_a_service_needs_grid_admin_to_touch_alert_rules(policy):
    """
    The `alerts.*` allow rules are all gated on `not is_service`, so the only rule a
    service can satisfy is the blanket `grid.admin` one. `grid.read` — enough to read
    every network's risk data — is not enough to read one operator's rules.
    """
    reader = make_service(scope="grid.read")
    admin = make_service(scope="grid.admin")

    assert not (await policy.allow_alerts_read(reader)).allowed
    assert not (await policy.allow_alerts_write(reader)).allowed
    assert (await policy.allow_alerts_read(admin)).allowed
    assert (await policy.allow_alerts_write(admin)).allowed


# ---------------------------------------------------------------------------
# The default
# ---------------------------------------------------------------------------


# @verifies REQ-0013
async def test_an_action_the_policy_does_not_name_is_denied(policy):
    """
    `default allow := false`. A route that grows a new action and forgets to add a rule
    for it is refused rather than waved through — which is the property that makes the
    permissive *fallback* survivable, since it only applies when no policy ran at all.
    """
    user = make_user(sub="alice", orgs={NETWORK: "dso"})

    decision = await policy._evaluate(user, "alerts.delete-everything", {})

    assert not decision.allowed
    assert decision.reason == "access denied"


# @verifies REQ-0013
async def test_an_evaluation_that_raises_is_allowed(policy, monkeypatch):
    """
    The fail-open branch, asserted so it is a decision on the record rather than a
    surprise. `Decision(True, "policy-error-permissive")` is the marker to grep for in
    production logs; a deployment that cannot tolerate it must assert on the reason.
    """

    def _boom(*_args, **_kwargs):
        raise RuntimeError("regorus exploded")

    monkeypatch.setattr(policy._engine, "evaluate_decision", _boom)

    decision = await policy.allow_network_read(make_user(sub="a", orgs={}), NETWORK)

    assert decision.allowed
    assert decision.reason == "policy-error-permissive"


# @verifies REQ-0013
async def test_a_policy_with_no_engine_allows_everything(policy_engine_is_loaded):
    """
    The other fail-open branch, and the reason `conftest.py` refuses to run without a
    loaded bundle: with `_engine is None` every check in this file would pass while
    proving the opposite of what it claims.
    """
    unloaded = GridAccessPolicy.__new__(GridAccessPolicy)
    unloaded._engine = None

    decision = await unloaded.allow_network_read(
        make_user(sub="alice", orgs={"anything": "dso"}), "someone-elses-network"
    )

    assert decision.allowed
    assert decision.reason == "no-policy-engine"
