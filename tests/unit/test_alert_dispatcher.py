"""Which rules fire, and what report gets sent when one does.

This is branching logic with no observer: it runs on an MQTT message, writes nothing to
the database, and reports itself only to the log. A rule that stops firing looks exactly
like a quiet week.

The Digital Twin and nudging-tool are faked; the database, the rules, the threshold
comparison and the per-unit / per-line summary are real.
"""

from __future__ import annotations

import pytest

from celine.grid.db.models import AlertRule, NotificationSettings
from celine.grid.services.alert_dispatcher import dispatch_grid_alerts
from celine.grid.services.risk_summary import (
    build_days,
    summarise,
    triggered_vectors,
)
from tests.fakes import FakeNudgingClient, FetchResult

NETWORK = "example-dso"
DATES = ["2026-09-11", "2026-09-12", "2026-09-13"]


def tratta(
    unit: str = "U1",
    line: str = "L1",
    *,
    vector: str = "wind",
    date: str = DATES[0],
    municipality: str = "M1",
    km_alert: float = 0.0,
    km_warning: float = 0.0,
    km_normal: float = 0.0,
    km_tree_high: float = 0.0,
) -> dict:
    """One `risk_km` row at tratta grain, as the Digital Twin returns it."""
    km_total = km_alert + km_warning + km_normal
    worst = "ALERT" if km_alert else "WARNING" if km_warning else "NORMAL"
    return {
        "date": date,
        "risk_vector": vector,
        "level": "tratta",
        "operational_unit": unit,
        "line_name": line,
        "municipality": municipality,
        "conductor_type": "overhead_bare" if vector == "wind" else "underground_cable",
        "segment_id": f"{line}|{municipality}",
        "km_total": km_total,
        "km_alert": km_alert,
        "km_warning": km_warning,
        "km_normal": km_normal,
        "km_escalated": 0.0,
        "km_tree_high": km_tree_high,
        "km_tree_mid": 0.0,
        "metric_max": 9.0,
        "worst_level": worst,
        # Postgres numeric reaches us as a string — the summary must not trust it
        "risk_index": "0.00",
    }


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


def rows(dt, *items: dict) -> None:
    dt.grid.set("fetch_values", FetchResult(list(items)))


async def dispatch(db, dt, nudging, **overrides) -> int:
    return await dispatch_grid_alerts(
        overrides.pop("network_id", NETWORK),
        dt,
        nudging,
        db,
        dates=overrides.pop("dates", DATES),
    )


# ---------------------------------------------------------------------------
# The threshold comparison — on km, not on row counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "threshold", "expected"),
    [
        (tratta(km_alert=1.0), "ALERT", ["wind"]),
        (tratta(km_warning=3.0), "ALERT", []),
        (tratta(km_warning=3.0), "WARNING", ["wind"]),
        (tratta(km_normal=50.0), "WARNING", []),
        (tratta(km_alert=0.0, km_normal=1.0), "ALERT", []),
    ],
    ids=["alert", "warning-below-alert", "warning-at-warning", "normal-only", "zero-km"],
)
# @verifies REQ-0032, REQ-0033
def test_a_level_counts_only_when_it_has_kilometres(row, threshold, expected):
    """
    The table carries km per level; a tratta with `km_alert = 0` is not at alert, whatever
    its `worst_level` string says. Testing the string would fire on a rounding artefact.
    """
    assert triggered_vectors([row], threshold, ["wind"]) == expected


# @verifies REQ-0032
def test_a_rule_watches_only_the_hazards_it_names():
    both = [tratta(km_alert=1.0, vector="wind"), tratta(km_alert=1.0, vector="heat")]
    assert triggered_vectors(both, "ALERT", ["heat"]) == ["heat"]
    assert triggered_vectors(both, "ALERT", ["wind", "heat"]) == ["wind", "heat"]


# @verifies REQ-0032
def test_an_unknown_threshold_falls_back_to_the_alert_floor():
    assert triggered_vectors([tratta(km_warning=5.0)], "SEVERE", ["wind"]) == []
    assert triggered_vectors([tratta(km_alert=5.0)], "SEVERE", ["wind"]) == ["wind"]


# @verifies REQ-0033
def test_a_row_missing_its_fields_is_survived():
    assert triggered_vectors([{}, {"risk_vector": "wind"}, {"km_alert": None}], "ALERT", ["wind"]) == []


# ---------------------------------------------------------------------------
# The summary — same arithmetic as the Digital Twin's line/unit grains
# ---------------------------------------------------------------------------


# @verifies REQ-0046
def test_units_sum_kilometres_and_recompute_the_index():
    out = summarise(
        [
            tratta("U1", "A", km_alert=5.0, km_normal=5.0),
            tratta("U1", "B", km_warning=10.0),
            tratta("U2", "C", km_normal=20.0),
        ],
        "operational_unit",
    )
    by_key = {u["key"]: u for u in out}
    assert by_key["U1"]["km_total"] == 20.0
    assert by_key["U1"]["km_alert"] == 5.0
    assert by_key["U1"]["km_warning"] == 10.0
    assert by_key["U1"]["risk_index"] == 50.0
    assert by_key["U1"]["worst_level"] == "ALERT"
    assert by_key["U1"]["pct_alert"] == 25.0
    assert by_key["U2"]["risk_index"] == 0.0
    assert by_key["U2"]["worst_level"] == "NORMAL"


# @verifies REQ-0046
def test_a_day_report_lists_units_by_index_and_the_worst_lines_at_or_above_the_threshold():
    data = [
        tratta("U1", "A", km_alert=5.0, km_normal=5.0),
        tratta("U2", "B", km_warning=10.0),
        tratta("U2", "C", km_normal=30.0),
        tratta("U1", "D", km_alert=1.0, vector="heat", date=DATES[1]),
    ]
    days = build_days(data, DATES, ["wind"], threshold="ALERT", top_n=15)

    assert [d["date"] for d in days] == DATES
    day = days[0]
    assert [v["risk_vector"] for v in day["vectors"]] == ["wind"]
    wind = day["vectors"][0]
    assert [u["operational_unit"] for u in wind["units"]] == ["U1", "U2"]
    assert wind["units"][0]["risk_index"] == 50.0
    assert wind["units"][0]["worst_lines"] == ["A"]
    # only lines with km at ALERT make the top list under an ALERT threshold
    assert [line["line_name"] for line in wind["top_lines"]] == ["A"]
    assert wind["totals"]["km_total"] == 50.0
    assert wind["totals"]["worst_level"] == "ALERT"
    # a day with nothing for the vector is still listed, empty
    assert days[2]["vectors"][0]["units"] == []
    # the heat row on day two is not in a wind-only report
    assert days[1]["vectors"][0]["top_lines"] == []


# @verifies REQ-0046
def test_the_warning_floor_admits_warning_lines_to_the_top_list():
    data = [tratta("U1", "A", km_warning=2.0), tratta("U1", "B", km_normal=9.0)]
    wind = build_days(data, DATES[:1], ["wind"], threshold="WARNING")[0]["vectors"][0]
    assert [line["line_name"] for line in wind["top_lines"]] == ["A"]
    assert wind["top_lines"][0]["operational_units"] == ["U1"]


# ---------------------------------------------------------------------------
# Which rules are considered
# ---------------------------------------------------------------------------


# @verifies REQ-0032
async def test_a_wind_rule_fires_on_wind_kilometres_at_alert(db, dt, nudging):
    await add_rule(db, risk_types=["wind"], threshold="ALERT")
    rows(dt, tratta(km_alert=2.0))

    assert await dispatch(db, dt, nudging) == 1
    assert nudging.payloads()[0]["facts"]["risk_types"] == ["wind"]


# @verifies REQ-0032
async def test_an_alert_rule_does_not_fire_on_a_warning(db, dt, nudging):
    await add_rule(db, threshold="ALERT")
    rows(dt, tratta(km_warning=20.0))

    assert await dispatch(db, dt, nudging) == 0
    assert nudging.events == []


# @verifies REQ-0032
async def test_a_rule_only_watches_the_hazards_it_names(db, dt, nudging):
    await add_rule(db, risk_types=["heat"])
    rows(dt, tratta(km_alert=5.0, vector="wind"))

    assert await dispatch(db, dt, nudging) == 0


# @verifies REQ-0034
async def test_a_rule_naming_both_hazards_reports_both_in_one_nudge(db, dt, nudging):
    await add_rule(db, risk_types=["wind", "heat"])
    rows(dt, tratta(km_alert=1.0, vector="wind"), tratta(km_alert=1.0, vector="heat"))

    assert await dispatch(db, dt, nudging) == 1
    facts = nudging.payloads()[0]["facts"]
    assert facts["risk_types"] == ["heat", "wind"]
    assert [v["risk_vector"] for v in facts["days"][0]["vectors"]] == ["heat", "wind"]


# @verifies REQ-0034
async def test_the_report_names_only_the_hazard_that_triggered(db, dt, nudging):
    await add_rule(db, risk_types=["wind", "heat"])
    rows(dt, tratta(km_alert=1.0, vector="wind"), tratta(km_normal=1.0, vector="heat"))

    await dispatch(db, dt, nudging)

    assert nudging.payloads()[0]["facts"]["risk_types"] == ["wind"]


# @verifies REQ-0031
async def test_an_inactive_rule_is_never_considered(db, dt, nudging):
    await add_rule(db, active=False)
    rows(dt, tratta(km_alert=9.0))

    assert await dispatch(db, dt, nudging) == 0
    assert dt.grid.calls == [], "no rule, no Digital Twin query"


# @verifies REQ-0030
async def test_only_rules_for_the_pipeline_s_network_are_considered(db, dt, nudging):
    await add_rule(db, network_id="other-dso")
    rows(dt, tratta(km_alert=9.0))

    assert await dispatch(db, dt, nudging) == 0


# @verifies REQ-0030
async def test_rules_backfilled_with_an_empty_network_id_never_fire(db, dt, nudging):
    await add_rule(db, network_id="", network_id_unset=True)
    rows(dt, tratta(km_alert=9.0))

    assert await dispatch(db, dt, nudging, network_id="") == 0


# @verifies REQ-0031
async def test_two_operators_with_different_recipients_each_get_a_nudge(db, dt, nudging):
    await add_rule(db, user_id="user-alice", recipients="alice@example.test")
    await add_rule(db, user_id="user-bob", recipients="bob@example.test")
    rows(dt, tratta(km_alert=1.0))

    assert await dispatch(db, dt, nudging) == 2


# @verifies REQ-0031
async def test_rules_sharing_a_recipient_list_are_merged_into_one_report(db, dt, nudging):
    """
    nudging-tool de-duplicates per day on (rule, user). Two rules — wind ALERT and heat
    WARNING — addressed to the same inbox would otherwise collapse to whichever was sent
    first, so the dispatcher merges them itself and sends one report naming both.
    """
    await add_rule(db, risk_types=["wind"], threshold="ALERT", recipients="ops@example.test")
    await add_rule(db, risk_types=["heat"], threshold="WARNING", recipients="OPS@example.test")
    rows(dt, tratta(km_alert=1.0, vector="wind"), tratta(km_warning=1.0, vector="heat"))

    assert await dispatch(db, dt, nudging) == 1
    facts = nudging.payloads()[0]["facts"]
    assert facts["risk_types"] == ["heat", "wind"]
    assert facts["threshold"] == "WARNING", "the lower floor of the merged rules"


# ---------------------------------------------------------------------------
# What is sent
# ---------------------------------------------------------------------------


# @verifies REQ-0037
async def test_the_report_carries_the_forecast_dates_and_the_scenario(db, dt, nudging):
    await add_rule(db)
    rows(dt, tratta(km_alert=1.0))

    await dispatch(db, dt, nudging)

    payload = nudging.payloads()[0]
    assert payload["event_type"] == "grid_risk_report"
    facts = payload["facts"]
    assert facts["scenario"] == "grid_risk_report"
    assert facts["facts_version"] == "1.0"
    assert facts["period"] == DATES[0]
    assert facts["dates"] == DATES
    assert facts["network_id"] == NETWORK
    assert facts["app_url"].startswith("http")
    assert facts["generated_at"]


# @verifies REQ-0037
async def test_the_digital_twin_is_asked_for_the_tratta_rows_of_those_dates(db, dt, nudging):
    await add_rule(db)
    rows(dt, tratta(km_alert=1.0))

    await dispatch(db, dt, nudging)

    _called, args, kwargs = dt.grid.calls[0]
    assert args[:2] == (NETWORK, "risk_km")
    assert args[2] == {"dates": DATES, "level": "tratta"}
    assert kwargs["limit"] == 20000


# @verifies REQ-0046
async def test_the_report_content_is_the_per_unit_and_per_line_summary(db, dt, nudging):
    await add_rule(db, threshold="ALERT")
    rows(
        dt,
        tratta("U1", "A", km_alert=5.0, km_normal=5.0),
        tratta("U2", "B", km_warning=10.0),
    )

    await dispatch(db, dt, nudging)

    day = nudging.payloads()[0]["facts"]["days"][0]
    wind = day["vectors"][0]
    assert wind["units"][0]["operational_unit"] == "U1"
    assert wind["units"][0]["km_alert"] == 5.0
    assert wind["top_lines"][0]["line_name"] == "A"
    assert wind["totals"]["km_alert"] == 5.0


# @verifies REQ-0037
async def test_without_dates_nothing_is_dispatched(db, dt, nudging):
    await add_rule(db)
    rows(dt, tratta(km_alert=9.0))

    assert await dispatch(db, dt, nudging, dates=[]) == 0
    assert dt.grid.calls == []


# @verifies REQ-0035
async def test_the_rule_s_own_recipients_win(db, dt, nudging):
    await add_rule(db, recipients="ops@example.test")
    db.add(NotificationSettings(user_id="user-alice", email_recipients="fallback@example.test"))
    await db.commit()
    rows(dt, tratta(km_alert=1.0))

    await dispatch(db, dt, nudging)

    assert nudging.payloads()[0]["facts"]["email_recipients"] == ["ops@example.test"]


# @verifies REQ-0035
async def test_a_rule_without_recipients_falls_back_to_the_user_s_settings(db, dt, nudging):
    await add_rule(db, recipients=None)
    db.add(NotificationSettings(user_id="user-alice", email_recipients="ops@example.test"))
    await db.commit()
    rows(dt, tratta(km_alert=1.0))

    await dispatch(db, dt, nudging)

    assert nudging.payloads()[0]["facts"]["email_recipients"] == ["ops@example.test"]


# @verifies REQ-0036
async def test_recipients_become_a_synthetic_user_id(db, dt, nudging):
    await add_rule(db, recipients="ops@example.test")
    rows(dt, tratta(km_alert=1.0))

    await dispatch(db, dt, nudging)

    assert nudging.payloads()[0]["user_id"].startswith("email-ingest:")


# @verifies REQ-0035
async def test_with_no_recipients_anywhere_the_nudge_is_addressed_to_the_operator(
    db, dt, nudging
):
    await add_rule(db, user_id="user-alice", recipients=None)
    rows(dt, tratta(km_alert=1.0))

    await dispatch(db, dt, nudging)

    payload = nudging.payloads()[0]
    assert payload["user_id"] == "user-alice"
    assert payload["facts"]["email_recipients"] == []


# ---------------------------------------------------------------------------
# When something upstream is missing
# ---------------------------------------------------------------------------


# @verifies REQ-0041
async def test_an_unavailable_digital_twin_sends_nothing_and_does_not_raise(db, dt, nudging):
    await add_rule(db)
    dt.grid.set("fetch_values", RuntimeError("DT down"))

    assert await dispatch(db, dt, nudging) == 0


# @verifies REQ-0041
async def test_an_empty_table_is_a_calm_day(db, dt, nudging):
    await add_rule(db)
    rows(dt)

    assert await dispatch(db, dt, nudging) == 0


# @verifies REQ-0038
async def test_one_failed_send_does_not_silence_the_rest(db, dt, nudging):
    await add_rule(db, user_id="user-alice", recipients="a@example.test")
    await add_rule(db, user_id="user-bob", recipients="b@example.test")
    rows(dt, tratta(km_alert=1.0))

    calls = {"n": 0}

    async def flaky(event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("nudging hiccup")
        nudging.events.append(event)

    nudging.ingest_event = flaky  # type: ignore[method-assign]

    assert await dispatch(db, dt, nudging) == 1
