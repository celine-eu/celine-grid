"""Alert rules and notification settings, end to end through the real app.

Everything here is real except the database dialect, the JWT signature check and the
Digital Twin: the routing, `PolicyMiddleware`, the OPA evaluation, the dependency chain
and the SQL are the shipped code.

**The confidentiality of a rule rests on the SQL, not on the policy.** `alerts.read` is
allowed for every non-service caller (see `tests/unit/test_policy.py`), so the
`WHERE user_id = :sub` in `api/alerts.py` is the whole of the isolation between two
operators — which is why it is tested from both sides here.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from celine.grid.db.models import AlertRule
from tests.conftest import NETWORK, OTHER_NETWORK, OTHER_SUB, USER_SUB


def rule_body(**overrides) -> dict:
    return {"risk_types": ["wind"], "threshold": "ALERT", **overrides}


async def create(client, headers, **overrides) -> dict:
    response = await client.post(
        "/api/alert-rules", json=rule_body(**overrides), headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------


# @verifies REQ-0014
async def test_a_new_rule_belongs_to_its_creator_and_their_network(client, dso_user):
    """
    Neither `user_id` nor `network_id` is accepted from the request body — both are
    taken from the token. A caller cannot file a rule against another DSO's grid or in
    another operator's name.
    """
    rule = await create(client, dso_user)

    assert rule["user_id"] == USER_SUB
    assert rule["network_id"] == NETWORK
    assert rule["active"] is True


# @verifies REQ-0014
async def test_the_network_in_the_body_is_ignored(client, dso_user):
    response = await client.post(
        "/api/alert-rules",
        json=rule_body(network_id="other-dso", user_id="somebody-else"),
        headers=dso_user,
    )

    assert response.status_code == 201
    assert response.json()["network_id"] == NETWORK
    assert response.json()["user_id"] == USER_SUB


# @verifies REQ-0011
async def test_a_caller_with_no_dso_organisation_cannot_create_a_rule(
    client, orgless_user
):
    """
    Refused by the policy, before `resolve_dso_network` is reached — a rule with no
    network would never fire, so there is nothing to write.
    """
    response = await client.post(
        "/api/alert-rules", json=rule_body(), headers=orgless_user
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"risk_types": [], "threshold": "ALERT"},
        {"risk_types": ["snow"], "threshold": "ALERT"},
        {"risk_types": ["wind"], "threshold": "URGENT"},
        {"risk_types": "wind", "threshold": "ALERT"},
        {"threshold": "ALERT"},
        {"risk_types": ["wind"]},
    ],
    ids=["empty", "unknown-hazard", "unknown-threshold", "not-a-list", "no-types", "no-threshold"],
)
# @verifies REQ-0017
async def test_a_rule_the_dispatcher_could_not_act_on_is_refused(
    client, dso_user, body
):
    """
    Each of these would produce a row the dispatcher silently ignores rather than an
    error anyone sees: an unknown hazard matches no distribution, and an unknown
    threshold falls back to the strictest floor. 422 at the edge is the only place the
    operator hears about it.
    """
    response = await client.post("/api/alert-rules", json=body, headers=dso_user)

    assert response.status_code == 422


# @verifies REQ-0017
async def test_both_hazards_in_one_rule_are_accepted(client, dso_user):
    rule = await create(client, dso_user, risk_types=["wind", "heat"])

    assert rule["risk_types"] == ["wind", "heat"]


# @verifies REQ-0018
async def test_a_rule_starts_active(client, dso_user):
    """
    An operator who files a rule expects it to be watching. `active` is opt-out.
    """
    assert (await create(client, dso_user))["active"] is True


# @verifies REQ-0018
async def test_a_rule_can_be_filed_switched_off(client, dso_user):
    assert (await create(client, dso_user, active=False))["active"] is False


# @verifies REQ-0018
async def test_deactivating_is_how_a_rule_is_stopped_without_losing_it(
    client, db, dso_user, dt
):
    """
    The dispatcher's `WHERE active IS TRUE` is the other half of this, and it is the
    only thing `active` does — the rule is still listed, still owned, still editable.
    """
    from tests.fakes import FakeNudgingClient
    from celine.grid.services.alert_dispatcher import dispatch_grid_alerts

    rule = await create(client, dso_user)
    await client.patch(
        f"/api/alert-rules/{rule['id']}", json={"active": False}, headers=dso_user
    )
    dt.grid.set("wind_alert_distribution", [{"risk_level": "ALERT", "events": 5}])

    sent = await dispatch_grid_alerts(
        NETWORK,
        dt,
        FakeNudgingClient(),
        db,
        period="2026-08-15",
        window_start="06:00",
        window_end="18:00",
    )

    assert sent == 0
    listed = (await client.get("/api/alert-rules", headers=dso_user)).json()
    assert len(listed) == 1


# ---------------------------------------------------------------------------
# Listing — the isolation between two operators
# ---------------------------------------------------------------------------


# @verifies REQ-0015
async def test_a_caller_sees_only_their_own_rules(client, dso_user, other_dso_user):
    """
    The whole of the isolation. `alerts.read` passes the policy for both callers; the
    `WHERE user_id = :sub` is what keeps them apart.
    """
    await create(client, dso_user)
    await create(client, other_dso_user)

    mine = await client.get("/api/alert-rules", headers=dso_user)

    assert [r["user_id"] for r in mine.json()] == [USER_SUB]


# @verifies REQ-0015
async def test_two_operators_of_the_same_dso_do_not_share_rules(client, jwt):
    """
    Same network, different `sub`. Rules are per-person, not per-network — which is
    also why the dispatcher sends one nudge per rule rather than per network.
    """
    from tests.fakes import make_user

    alice = jwt.headers(make_user(sub="alice", orgs={NETWORK: "dso"}))
    bob = jwt.headers(make_user(sub="bob", orgs={NETWORK: "dso"}))

    await create(client, alice)
    listed = await client.get("/api/alert-rules", headers=bob)

    assert listed.json() == []


# @verifies REQ-0015
async def test_rules_are_listed_oldest_first(client, dso_user):
    """
    `ORDER BY created_at`. The column has second granularity on PostgreSQL's
    `now()`, so rules created within the same second tie and their order is whatever
    the database returns — visible only as a wobble in the settings list.
    """
    first = await create(client, dso_user, threshold="ALERT")
    second = await create(client, dso_user, threshold="WARNING")

    listed = (await client.get("/api/alert-rules", headers=dso_user)).json()

    assert {r["id"] for r in listed} == {first["id"], second["id"]}


# @verifies REQ-0010
async def test_an_orgless_caller_may_still_list_and_simply_has_nothing(
    client, orgless_user
):
    """
    Reading is open to any authenticated non-service caller and the result is empty
    rather than a 403. The policy permits it; the SQL makes it uninteresting.
    """
    response = await client.get("/api/alert-rules", headers=orgless_user)

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Updating and deleting
# ---------------------------------------------------------------------------


# @verifies REQ-0019
async def test_a_partial_update_changes_only_what_was_sent(client, dso_user):
    """
    `exclude_unset=True`. An absent field is left alone rather than reset to its
    default — the difference between a frontend that must send the whole rule back and
    one that can send a toggle.
    """
    rule = await create(client, dso_user, risk_types=["wind", "heat"], recipients="a@b.test")

    updated = await client.patch(
        f"/api/alert-rules/{rule['id']}", json={"active": False}, headers=dso_user
    )

    assert updated.status_code == 200
    assert updated.json()["active"] is False
    assert updated.json()["risk_types"] == ["wind", "heat"]
    assert updated.json()["recipients"] == "a@b.test"


# @verifies REQ-0016
async def test_another_operator_s_rule_is_not_found_rather_than_forbidden(
    client, dso_user, other_dso_user
):
    """
    404, not 403. A 403 would confirm the rule exists, which is a rule id an operator
    of another DSO has no business confirming.
    """
    rule = await create(client, dso_user)

    patched = await client.patch(
        f"/api/alert-rules/{rule['id']}", json={"active": False}, headers=other_dso_user
    )
    deleted = await client.delete(
        f"/api/alert-rules/{rule['id']}", headers=other_dso_user
    )

    assert patched.status_code == 404
    assert deleted.status_code == 404


# @verifies REQ-0016
async def test_another_operator_s_rule_survives_the_attempt(
    client, db, dso_user, other_dso_user
):
    """
    The 404 above is asserted at the database as well as at the response, because a
    delete that answered 404 *after* deleting would look identical from outside.
    """
    rule = await create(client, dso_user)

    await client.delete(f"/api/alert-rules/{rule['id']}", headers=other_dso_user)

    surviving = (await db.execute(select(AlertRule))).scalars().all()
    assert [str(r.id) for r in surviving] == [rule["id"]]


# @verifies REQ-0016
async def test_a_rule_that_never_existed_is_a_404(client, dso_user):
    response = await client.patch(
        f"/api/alert-rules/{uuid.uuid4()}", json={"active": False}, headers=dso_user
    )

    assert response.status_code == 404


# @verifies REQ-0016
async def test_a_malformed_rule_id_is_a_422(client, dso_user):
    response = await client.delete("/api/alert-rules/not-a-uuid", headers=dso_user)

    assert response.status_code == 422


# @verifies REQ-0016
async def test_deleting_a_rule_removes_it(client, db, dso_user):
    rule = await create(client, dso_user)

    response = await client.delete(f"/api/alert-rules/{rule['id']}", headers=dso_user)

    assert response.status_code == 204
    assert (await db.execute(select(AlertRule))).scalars().all() == []


# @verifies REQ-0011
async def test_an_orgless_caller_cannot_delete_even_their_own_rule(client, jwt):
    """
    `alerts.write` requires a DSO organisation, and delete is a write. An operator
    removed from their organisation in Keycloak keeps their rules and loses the ability
    to turn them off — and the rules keep firing, because the dispatcher never consults
    the policy. See `.agents/knowledge/a-rule-outlives-its-author.md`.
    """
    from tests.fakes import make_user

    alice = jwt.headers(make_user(sub="alice", orgs={NETWORK: "dso"}))
    rule = await create(client, alice)

    demoted = jwt.headers(make_user(sub="alice", orgs={}))
    response = await client.delete(f"/api/alert-rules/{rule['id']}", headers=demoted)

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Notification settings
# ---------------------------------------------------------------------------


# @verifies REQ-0020
async def test_reading_settings_creates_an_empty_row(client, db, dso_user):
    """
    A GET that writes. It keeps the frontend from having to distinguish "never
    configured" from "configured empty", at the cost of a row per caller who ever
    opened the settings page.
    """
    response = await client.get("/api/notification-settings", headers=dso_user)

    assert response.status_code == 200
    assert response.json()["user_id"] == USER_SUB
    assert response.json()["email_recipients"] is None


# @verifies REQ-0021
async def test_settings_are_per_user(client, dso_user, other_dso_user):
    await client.put(
        "/api/notification-settings",
        json={"email_recipients": "alice@example.test"},
        headers=dso_user,
    )

    theirs = await client.get("/api/notification-settings", headers=other_dso_user)

    assert theirs.json()["user_id"] == OTHER_SUB
    assert theirs.json()["email_recipients"] is None


# @verifies REQ-0020
async def test_settings_can_be_written_before_they_are_read(client, dso_user):
    """
    The PUT creates the row too, so a caller who configures without ever having opened
    the page is not a 404.
    """
    response = await client.put(
        "/api/notification-settings",
        json={"email_recipients": "ops@example.test"},
        headers=dso_user,
    )

    assert response.status_code == 200
    assert response.json()["email_recipients"] == "ops@example.test"


# @verifies REQ-0021
async def test_a_partial_settings_write_leaves_the_other_field_alone(client, dso_user):
    """
    A PUT that behaves like a PATCH: `exclude_unset=True` again. The verb says replace
    and the implementation merges.
    """
    await client.put(
        "/api/notification-settings",
        json={"email_recipients": "ops@example.test", "webhook_url": "https://hook.test"},
        headers=dso_user,
    )

    updated = await client.put(
        "/api/notification-settings",
        json={"webhook_url": "https://other.test"},
        headers=dso_user,
    )

    assert updated.json()["email_recipients"] == "ops@example.test"
    assert updated.json()["webhook_url"] == "https://other.test"


# @verifies REQ-0035
async def test_settings_recipients_reach_the_dispatcher(client, db, dso_user, dt):
    """
    The join the two halves of this service make: what the API writes is what the MQTT
    listener reads, and nothing else connects them.
    """
    from tests.fakes import FakeNudgingClient
    from celine.grid.services.alert_dispatcher import dispatch_grid_alerts

    await client.put(
        "/api/notification-settings",
        json={"email_recipients": "ops@example.test"},
        headers=dso_user,
    )
    await create(client, dso_user)
    dt.grid.set("wind_alert_distribution", [{"risk_level": "ALERT", "events": 1}])

    nudging = FakeNudgingClient()
    sent = await dispatch_grid_alerts(
        NETWORK,
        dt,
        nudging,
        db,
        period="2026-08-15",
        window_start="06:00",
        window_end="18:00",
    )

    assert sent == 1
    assert nudging.payloads()[0]["facts"]["email_recipients"] == ["ops@example.test"]
