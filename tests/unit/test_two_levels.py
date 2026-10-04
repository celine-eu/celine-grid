"""Two levels and nothing between them: what a token's realm part does here (nothing).

The platform has exactly two kinds of grant. The realm role `platform-admin` is the only
platform-wide one; an organisation's own groups (`admins > managers > editors > viewers`)
count only inside that organisation. Realm groups are gone, and a token that still
carries one must grant nothing.

This service has no platform-wide grant at all (REQ-0051), so the realm part of a token —
role or group, current or legacy — must not reach the policy, and an organisation's
groups must reach it only for the organisation the request concerns.

The claim shapes below are the ones the local Keycloak issues through `oauth2_proxy`
(organisation groups keep Keycloak's leading slash; the legacy token carried both the
full-path and the bare form of the realm group). `tests/integration/test_real_tokens.py`
repeats the decisive checks with real tokens.
"""

from __future__ import annotations

import pytest
from celine.sdk.policies import SubjectType

from celine.grid.security.policy import GridAccessPolicy, _make_policy_input
from tests.fakes import make_service, make_user

NETWORK = "example-dso"
OTHER_NETWORK = "other-dso"

# A token from before the realm groups were removed: realm group `/admins` in both forms,
# and the realm role `admin` that was mapped onto it.
LEGACY_REALM_ADMIN = {
    "groups": ["/admins", "admins"],
    "realm_access": {"roles": ["admin"]},
}
PLATFORM_ADMIN = {"realm_access": {"roles": ["platform-admin"]}}
ORDINARY_ROLES = {
    "realm_access": {"roles": ["default-roles-celine", "offline_access", "uma_authorization"]}
}


@pytest.fixture
def policy(policy_engine_is_loaded) -> GridAccessPolicy:
    p = GridAccessPolicy()
    assert p._engine is not None
    return p


def _groups(user, action="read", attributes=None):
    return _make_policy_input(user, action, attributes or {}).subject.groups


# ---------------------------------------------------------------------------
# Organisation groups: one organisation, the one the request concerns
# ---------------------------------------------------------------------------


# @verifies REQ-0051
def test_the_groups_are_those_of_the_requested_network_only():
    """A member of two organisations brings only the requested one's groups."""
    user = make_user(
        sub="alice",
        orgs={NETWORK: "dso", "example-rec": "rec"},
        org_groups={NETWORK: ["/viewers"], "example-rec": ["/admins"]},
        **ORDINARY_ROLES,
    )

    assert _groups(user, attributes={"network_id": NETWORK}) == ["viewers"]


# @verifies REQ-0051
def test_a_request_for_a_network_the_caller_is_not_in_carries_no_groups():
    user = make_user(
        sub="alice",
        orgs={NETWORK: "dso", "example-rec": "rec"},
        org_groups={NETWORK: ["/admins"], "example-rec": ["/admins"]},
    )

    assert _groups(user, attributes={"network_id": OTHER_NETWORK}) == []


# @verifies REQ-0051
def test_an_action_that_names_no_network_uses_the_caller_s_own_dso():
    user = make_user(
        sub="alice",
        orgs={"example-rec": "rec", NETWORK: "dso"},
        org_groups={"example-rec": ["/admins"], NETWORK: ["/managers"]},
    )

    assert _groups(user, action="alerts.write") == ["managers"]


# @verifies REQ-0051
def test_a_service_account_carries_no_groups():
    subject = _make_policy_input(
        make_service(scope="grid.read"), "read", {"network_id": NETWORK}
    ).subject

    assert subject.type is SubjectType.SERVICE
    assert subject.groups == []


# ---------------------------------------------------------------------------
# The realm level grants nothing here
# ---------------------------------------------------------------------------


# @verifies REQ-0051
async def test_a_realm_group_still_in_a_token_grants_nothing(policy):
    """
    The old realm `/admins` (both forms) and realm role `admin`, with an organisation
    that is not a DSO: not a network reader, not an alert-rule writer, and none of it
    reaches the policy input.
    """
    user = make_user(
        sub="legacy-admin",
        orgs={"example-rec": "rec"},
        org_groups={"example-rec": ["/admins"]},
        **LEGACY_REALM_ADMIN,
    )

    assert not user.is_platform_admin
    assert _groups(user, attributes={"network_id": NETWORK}) == []
    assert _groups(user, action="alerts.write") == []
    assert not (await policy.allow_network_read(user, NETWORK)).allowed
    assert not (await policy.allow_alerts_write(user)).allowed


# @verifies REQ-0051
def test_a_realm_group_is_never_mixed_into_an_organisation_s_groups():
    """A DSO viewer who also holds the legacy realm `/admins` is still only a viewer."""
    user = make_user(
        sub="legacy-viewer",
        orgs={NETWORK: "dso"},
        org_groups={NETWORK: ["/viewers"]},
        **LEGACY_REALM_ADMIN,
    )

    assert _groups(user, attributes={"network_id": NETWORK}) == ["viewers"]


# @verifies REQ-0051
async def test_an_organisation_admin_is_not_a_platform_admin(policy):
    """A DSO's own `admins` reaches its own network and no other."""
    user = make_user(
        sub="org-admin",
        orgs={NETWORK: "dso"},
        org_groups={NETWORK: ["/admins"]},
        **ORDINARY_ROLES,
    )

    assert not user.is_platform_admin
    assert (await policy.allow_network_read(user, NETWORK)).allowed
    assert not (await policy.allow_network_read(user, OTHER_NETWORK)).allowed


# @verifies REQ-0051
async def test_a_platform_admin_with_no_dso_reaches_no_network(policy):
    """`platform-admin` is a platform grant, and this service has none to give."""
    user = make_user(sub="admin", orgs={}, **PLATFORM_ADMIN)

    assert user.is_platform_admin
    subject = _make_policy_input(user, "read", {"network_id": NETWORK}).subject
    assert "platform-admin" not in subject.groups
    assert subject.groups == []
    assert not (await policy.allow_network_read(user, NETWORK)).allowed
    assert not (await policy.allow_alerts_write(user)).allowed


# @verifies REQ-0051
async def test_a_platform_admin_reads_only_the_dso_it_is_a_member_of(policy):
    user = make_user(
        sub="admin",
        orgs={NETWORK: "dso"},
        org_groups={NETWORK: ["/admins"]},
        **PLATFORM_ADMIN,
    )

    assert user.is_platform_admin
    assert _groups(user, attributes={"network_id": NETWORK}) == ["admins"]
    assert (await policy.allow_network_read(user, NETWORK)).allowed
    assert not (await policy.allow_network_read(user, OTHER_NETWORK)).allowed
