"""Which rules fire, and what gets sent when one does.

This is branching logic with no observer: it runs on an MQTT message, writes nothing to
the database, and reports itself only to the log. A rule that stops firing looks exactly
like a quiet week.

The Digital Twin and nudging-tool are faked; the database, the rules and the threshold
comparison are real.
"""

from __future__ import annotations

import pytest

from celine.grid.db.models import AlertRule, NotificationSettings
from celine.grid.services.alert_dispatcher import (
    _event_type_from_triggered_types,
    _has_events,
    dispatch_grid_alerts,
)
from tests.fakes import FakeDTClient, FakeNudgingClient

NETWORK = "example-dso"
WINDOW = {"period": "2026-08-15", "window_start": "06:00", "window_end": "18:00"}


def distribution(**levels: int) -> list[dict]:
    """A DT alert distribution: one row per risk level, carrying an event count."""
    return [
        {"risk_level": level, "events": count} for level, count in levels.items()
    ]


async def add_rule(db, **overrides) -> AlertRule:
    rule = AlertRule(
        **{
            "user_id": "user-alice",
            "network_id": NETWORK,
            "risk_types": ["wind"],
            "threshold": "ALERT",
            "active": True,
            **overrides,
        }
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@pytest.fixture
def nudging() -> FakeNudgingClient:
    return FakeNudgingClient()


async def dispatch(db, dt, nudging, **overrides) -> int:
    return await dispatch_grid_alerts(
        overrides.pop("network_id", NETWORK), dt, nudging, db, **{**WINDOW, **overrides}
    )


# ---------------------------------------------------------------------------
# The threshold comparison
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("levels", "wanted", "expected"),
    [
        ({"ALERT": 3}, {"ALERT"}, True),
        ({"WARNING": 3}, {"ALERT"}, False),
        ({"WARNING": 3}, {"WARNING", "ALERT"}, True),
        ({"ALERT": 0}, {"ALERT"}, False),
        ({"LOW": 99}, {"WARNING", "ALERT"}, False),
    ],
    ids=["alert", "warning-below-alert", "warning-at-warning", "zero", "low-only"],
)
# @verifies REQ-0033
def test_a_level_counts_only_when_it_has_events(levels, wanted, expected):
    """
    `events: 0` is a row the Digital Twin returns for a level with nothing in it. A
    presence check on the row rather than on the count would fire every rule, every run.
    """
    assert _has_events(distribution(**levels), wanted) is expected


# @verifies REQ-0033
def test_the_risk_level_is_compared_case_insensitively():
    """
    The comparison upper-cases whatever the DT sent. Worth pinning: the levels are a
    string field crossing a service boundary, and a lower-case `"alert"` silently
    matching nothing would disable every rule at once.
    """
    assert _has_events([{"risk_level": "alert", "events": 1}], {"ALERT"}) is True


# @verifies REQ-0033
def test_a_row_missing_its_fields_is_survived():
    assert _has_events([{}, {"risk_level": None}, {"events": None}], {"ALERT"}) is False


@pytest.mark.parametrize(
    ("triggered", "expected"),
    [(["wind"], "wind"), (["heat"], "heat"), (["wind", "heat"], "thunderstorm")],
    ids=["wind", "heat", "both"],
)
# @verifies REQ-0034
def test_two_hazards_at_once_are_reported_as_a_thunderstorm(triggered, expected):
    """
    `thunderstorm` is nudging-tool's vocabulary for a combined event, and it is also the
    fallback for an empty list — which cannot occur, because a rule with no triggered
    types is skipped before this is called.
    """
    assert _event_type_from_triggered_types(triggered) == expected


# ---------------------------------------------------------------------------
# Which rules are considered
# ---------------------------------------------------------------------------


# @verifies REQ-0032
async def test_a_wind_rule_fires_on_wind_alerts(db, dt, nudging):
    await add_rule(db, risk_types=["wind"], threshold="ALERT")
    dt.grid.set("wind_alert_distribution", distribution(ALERT=2))

    assert await dispatch(db, dt, nudging) == 1
    assert nudging.payloads()[0]["facts"]["event_type"] == "wind"


# @verifies REQ-0032
async def test_a_warning_rule_fires_on_alert_level_too(db, dt, nudging):
    """
    `WARNING` is a floor, not an equality: an operator asking to hear about warnings
    certainly wants to hear about alerts.
    """
    await add_rule(db, threshold="WARNING")
    dt.grid.set("wind_alert_distribution", distribution(ALERT=1))

    assert await dispatch(db, dt, nudging) == 1


# @verifies REQ-0032
async def test_an_alert_rule_does_not_fire_on_a_warning(db, dt, nudging):
    await add_rule(db, threshold="ALERT")
    dt.grid.set("wind_alert_distribution", distribution(WARNING=5))

    assert await dispatch(db, dt, nudging) == 0
    assert nudging.events == []


# @verifies REQ-0032
async def test_a_rule_only_watches_the_hazards_it_names(db, dt, nudging):
    """
    A wind rule is not woken by a heat wave.
    """
    await add_rule(db, risk_types=["wind"])
    dt.grid.set("wind_alert_distribution", distribution(LOW=9))
    dt.grid.set("heat_alert_distribution", distribution(ALERT=4))

    assert await dispatch(db, dt, nudging) == 0


# @verifies REQ-0034
async def test_a_rule_naming_both_hazards_fires_once_for_both(db, dt, nudging):
    """
    One nudge, not two — and the hazard it names is `thunderstorm`.
    """
    await add_rule(db, risk_types=["wind", "heat"])
    dt.grid.set("wind_alert_distribution", distribution(ALERT=1))
    dt.grid.set("heat_alert_distribution", distribution(ALERT=1))

    assert await dispatch(db, dt, nudging) == 1
    assert nudging.payloads()[0]["facts"]["event_type"] == "thunderstorm"


# @verifies REQ-0031
async def test_an_inactive_rule_is_never_considered(db, dt, nudging):
    """
    `active` is the operator's off switch and it is applied in SQL, so an inactive rule
    is not even loaded.
    """
    await add_rule(db, active=False)
    dt.grid.set("wind_alert_distribution", distribution(ALERT=9))

    assert await dispatch(db, dt, nudging) == 0


# @verifies REQ-0030
async def test_only_rules_for_the_pipeline_s_network_are_considered(db, dt, nudging):
    """
    The `network_id` comes from the pipeline event's namespace. A rule belonging to
    another DSO must not be evaluated against this network's distributions — it would
    mail one operator about another operator's grid.
    """
    await add_rule(db, network_id="other-dso")
    dt.grid.set("wind_alert_distribution", distribution(ALERT=9))

    assert await dispatch(db, dt, nudging) == 0


# @verifies REQ-0030
async def test_rules_backfilled_with_an_empty_network_id_never_fire(db, dt, nudging):
    """
    Migration 002 backfilled pre-existing rules with `network_id = ''` and migration 003
    flagged them `network_id_unset`. Nothing reads that flag — but nothing needs to,
    because `''` matches no real network alias, so those rules are inert. Pinned so the
    day someone "fixes" the flag it is clear what the flag was for. See
    `.agents/knowledge/unattributed-alert-rules-are-inert.md`.
    """
    await add_rule(db, network_id="", network_id_unset=True)
    dt.grid.set("wind_alert_distribution", distribution(ALERT=9))

    assert await dispatch(db, dt, nudging) == 0


# @verifies REQ-0031
async def test_every_matching_rule_gets_its_own_nudge(db, dt, nudging):
    await add_rule(db, user_id="user-alice")
    await add_rule(db, user_id="user-bob")
    dt.grid.set("wind_alert_distribution", distribution(ALERT=1))

    assert await dispatch(db, dt, nudging) == 2


# ---------------------------------------------------------------------------
# What is sent
# ---------------------------------------------------------------------------


# @verifies REQ-0037
async def test_the_pipeline_window_is_carried_into_the_nudge(db, dt, nudging):
    """
    nudging-tool renders the window into the message an operator reads, so these three
    fields are the difference between "high wind risk" and "high wind risk between
    06:00 and 18:00 today".
    """
    await add_rule(db)
    dt.grid.set("wind_alert_distribution", distribution(ALERT=1))

    await dispatch(db, dt, nudging)

    facts = nudging.payloads()[0]["facts"]
    assert facts["period"] == "2026-08-15"
    assert facts["window_start"] == "06:00"
    assert facts["window_end"] == "18:00"
    assert facts["scenario"] == "extr_event"
    assert facts["facts_version"] == "1.0"


# @verifies REQ-0037
async def test_the_event_type_is_extr_event(db, dt, nudging):
    """
    `extr_event` — not `grid_alert`. It is the scenario key nudging-tool matches its
    templates on, so it is a contract with another repository and not a label.
    """
    await add_rule(db)
    dt.grid.set("wind_alert_distribution", distribution(ALERT=1))

    await dispatch(db, dt, nudging)

    assert nudging.payloads()[0]["event_type"] == "extr_event"


# @verifies REQ-0035
async def test_the_rule_s_own_recipients_win(db, dt, nudging):
    await add_rule(db, recipients="ops@example.test")
    db.add(
        NotificationSettings(user_id="user-alice", email_recipients="fallback@example.test")
    )
    await db.commit()
    dt.grid.set("wind_alert_distribution", distribution(ALERT=1))

    await dispatch(db, dt, nudging)

    assert nudging.payloads()[0]["facts"]["email_recipients"] == ["ops@example.test"]


# @verifies REQ-0035
async def test_a_rule_without_recipients_falls_back_to_the_user_s_settings(
    db, dt, nudging
):
    """
    The per-rule field is an override. The settings row is where an operator sets it
    once for everything.
    """
    await add_rule(db, recipients=None)
    db.add(
        NotificationSettings(user_id="user-alice", email_recipients="ops@example.test")
    )
    await db.commit()
    dt.grid.set("wind_alert_distribution", distribution(ALERT=1))

    await dispatch(db, dt, nudging)

    assert nudging.payloads()[0]["facts"]["email_recipients"] == ["ops@example.test"]


# @verifies REQ-0036
async def test_recipients_become_a_synthetic_user_id(db, dt, nudging):
    await add_rule(db, recipients="ops@example.test")
    dt.grid.set("wind_alert_distribution", distribution(ALERT=1))

    await dispatch(db, dt, nudging)

    assert nudging.payloads()[0]["user_id"].startswith("email-ingest:")


# @verifies REQ-0035
async def test_with_no_recipients_anywhere_the_nudge_is_addressed_to_the_operator(
    db, dt, nudging
):
    """
    `rule.user_id` is the Keycloak `sub`, which is what nudging-tool resolves to a
    person. The alert is not dropped for want of an email address.
    """
    await add_rule(db, user_id="user-alice", recipients=None)
    dt.grid.set("wind_alert_distribution", distribution(ALERT=1))

    await dispatch(db, dt, nudging)

    payload = nudging.payloads()[0]
    assert payload["user_id"] == "user-alice"
    assert payload["facts"]["email_recipients"] == []


# ---------------------------------------------------------------------------
# When something upstream is missing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["period", "window_start", "window_end"]
)
# @verifies REQ-0037
async def test_incomplete_window_metadata_cancels_the_whole_dispatch(
    db, dt, nudging, missing
):
    """
    Before any DT call. A nudge whose window says `None` is worse than no nudge, and a
    pipeline event that lost its metadata is a fault upstream — the log line is the
    only trace, so grep for it when alerts stop arriving.
    """
    await add_rule(db)
    dt.grid.set("wind_alert_distribution", distribution(ALERT=9))

    assert await dispatch(db, dt, nudging, **{missing: None}) == 0
    assert dt.grid.calls == []


# @verifies REQ-0041
async def test_one_hazard_failing_does_not_stop_the_other(db, dt, nudging):
    """
    The two distributions are fetched independently and each failure is swallowed. A
    heat rule still fires when the wind query is down.
    """
    await add_rule(db, risk_types=["heat"])
    dt.grid.set("wind_alert_distribution", RuntimeError("DT wind endpoint down"))
    dt.grid.set("heat_alert_distribution", distribution(ALERT=1))

    assert await dispatch(db, dt, nudging) == 1


# @verifies REQ-0041
async def test_no_distribution_data_at_all_dispatches_nothing(db, dt, nudging):
    """
    **Empty and unavailable are the same observation here**, and both mean "send
    nothing". A Digital Twin that is down therefore looks exactly like a calm day, and
    no alert is raised about the absence. See
    `.agents/knowledge/silence-is-the-failure-mode.md`.
    """
    await add_rule(db)
    dt.grid.set("wind_alert_distribution", RuntimeError("down"))
    dt.grid.set("heat_alert_distribution", RuntimeError("down"))

    assert await dispatch(db, dt, nudging) == 0


# @verifies REQ-0038
async def test_a_failed_send_does_not_stop_the_remaining_rules(db, dt, nudging):
    """
    Each `ingest_event` is caught individually, so one operator's undeliverable nudge
    does not silence the rest of the network's.
    """
    await add_rule(db, user_id="user-alice")
    await add_rule(db, user_id="user-bob")
    dt.grid.set("wind_alert_distribution", distribution(ALERT=1))
    failing = FakeNudgingClient(fails=True)

    # Nothing raises out of the dispatcher, and the count reports what was *sent*.
    assert await dispatch(db, dt, failing) == 0


# @verifies REQ-0031
async def test_a_network_with_no_rules_makes_no_further_work(db, dt, nudging):
    dt.grid.set("wind_alert_distribution", distribution(ALERT=9))

    assert await dispatch(db, dt, nudging) == 0
    assert nudging.events == []


# @verifies REQ-0032
async def test_an_unknown_threshold_is_treated_as_alert(db, dt, nudging):
    """
    `_THRESHOLD_FLOOR.get(rule.threshold, {"ALERT"})`. Nothing but the API validator
    stops a row from carrying something else — a hand-written INSERT, or a future
    threshold added to the schema but not here — and the fallback is the strict one,
    which under-alerts rather than over-alerts.
    """
    await add_rule(db, threshold="EVERYTHING")
    dt.grid.set("wind_alert_distribution", distribution(WARNING=9))

    assert await dispatch(db, dt, nudging) == 0
