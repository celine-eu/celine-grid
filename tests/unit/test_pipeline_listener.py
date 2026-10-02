"""What arrives on `celine/pipelines/runs/+`, and what this service does about it.

This is the only entry point to the service that is not an HTTP request, and the only
one with no caller to report an error to: everything it rejects, it rejects silently
into the log.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from celine.sdk.broker import PipelineRunEvent

from celine.grid.db.models import AlertRule
from celine.grid.services import pipeline_listener as listener
from celine.grid.services.pipeline_listener import (
    _horizon_dates,
    _normalise_period,
    on_pipeline_run,
)
from celine.grid.settings import settings

NETWORK = "example-dso"


class Message:
    """Shaped like `celine.sdk.broker.ReceivedMessage` as the handler reads one."""

    def __init__(self, payload: dict, topic: str = "celine/pipelines/runs/1") -> None:
        self.payload = payload
        self.topic = topic


def run_payload(**overrides) -> dict:
    return {
        "flow": settings.grid_pipeline_flow,
        "status": "completed",
        "namespace": NETWORK,
        "run_id": "3f2c1e00-0000-4000-8000-000000000001",
        "timestamp": "2026-08-15T06:30:00Z",
        **overrides,
    }


@pytest.fixture
def dispatched(monkeypatch, db_sessionmaker):
    """Record every `dispatch_grid_alerts` call the handler makes.

    The dispatcher itself has its own file; here the question is only whether the
    handler decided to call it, and with what.
    """
    calls: list[dict] = []

    async def _dispatch(network_id, dt, nudging, session, **kwargs):
        calls.append({"network_id": network_id, **kwargs})
        return len(calls)

    monkeypatch.setattr(listener, "dispatch_grid_alerts", _dispatch)
    monkeypatch.setattr(listener, "AsyncSessionLocal", db_sessionmaker)
    monkeypatch.setattr(listener, "_dt_client", object())
    monkeypatch.setattr(listener, "_nudging_client", object())
    monkeypatch.setattr(settings, "grid_alerts_enabled", True)
    return calls


# ---------------------------------------------------------------------------
# Which messages are acted on
# ---------------------------------------------------------------------------


# @verifies REQ-0029
async def test_a_completed_grid_run_dispatches(dispatched):
    await on_pipeline_run(Message(run_payload()))

    assert len(dispatched) == 1
    assert dispatched[0]["network_id"] == NETWORK


@pytest.mark.parametrize("status", ["running", "failed", "crashed", "cancelled"])
# @verifies REQ-0029
async def test_only_a_completed_run_dispatches(dispatched, status):
    """
    A failed run leaves the DT holding the *previous* run's data, so alerting on it
    would re-send yesterday's risk as though it were today's.
    """
    await on_pipeline_run(Message(run_payload(status=status)))

    assert dispatched == []


# @verifies REQ-0029
async def test_another_flow_s_run_is_ignored(dispatched):
    """
    The subscription is `celine/pipelines/runs/+` — every pipeline in the platform. The
    flow name is the only filter, and it is configurable through `GRID_PIPELINE_FLOW`,
    so a rename in `../celine-pipelines` silently stops all alerting here.
    """
    await on_pipeline_run(Message(run_payload(flow="some-other-flow")))

    assert dispatched == []


# @verifies REQ-0030
async def test_a_namespace_that_is_not_the_pipeline_s_is_taken_as_a_network_id(dispatched):
    """
    The legacy contract: a run published under a DSO alias evaluates that network only.
    """
    await on_pipeline_run(Message(run_payload(namespace="other-dso")))

    assert [c["network_id"] for c in dispatched] == ["other-dso"]


# @verifies REQ-0030
async def test_the_pipeline_s_own_namespace_fans_out_to_every_network_with_an_active_rule(
    dispatched, db
):
    """
    celine-utils publishes the grid flow under `get_namespace("grid")`, which is `grid`
    (or `<base>.grid`), never a DSO alias. The networks to evaluate are therefore read
    from the rules themselves — one dispatch per distinct `network_id`, inactive rules
    and the migration-002 blanks excluded.
    """
    for network, active in (("dso-a", True), ("dso-a", True), ("dso-b", True), ("dso-c", False), ("", True)):
        db.add(AlertRule(user_id="u", network_id=network, risk_types=["wind"], threshold="ALERT", active=active))
    await db.commit()

    await on_pipeline_run(Message(run_payload(namespace=settings.grid_pipeline_namespace)))
    assert sorted(c["network_id"] for c in dispatched) == ["dso-a", "dso-b"]

    dispatched.clear()
    await on_pipeline_run(Message(run_payload(namespace="celine." + settings.grid_pipeline_namespace)))
    assert sorted(c["network_id"] for c in dispatched) == ["dso-a", "dso-b"]


# @verifies REQ-0037
async def test_the_dispatch_carries_the_run_date_and_the_horizon(dispatched):
    """
    The forecast tables hold today + 2 days; the rules look at exactly that window,
    counted from the run's own timestamp so a late re-run still evaluates its day.
    """
    await on_pipeline_run(Message(run_payload(timestamp="2026-08-15T06:30:00Z")))

    assert dispatched[0]["dates"] == ["2026-08-15", "2026-08-16", "2026-08-17"]


# @verifies REQ-0029
async def test_an_unparseable_message_is_dropped(dispatched):
    """
    Anything can be published to the topic. A malformed payload is logged and dropped —
    it must never take the listener down, because there is no supervisor to restart it
    and the subscription would be lost for the life of the process.
    """
    await on_pipeline_run(Message({"nonsense": True}))
    await on_pipeline_run(Message({}))

    assert dispatched == []


# @verifies REQ-0040
async def test_nothing_dispatches_before_the_clients_are_built(monkeypatch, db_sessionmaker):
    """
    `create_broker()` builds the DT and nudging clients, and it is not called when
    `DIGITAL_TWIN_API_URL` is unset. The handler checks rather than raising into the
    broker's callback.
    """
    monkeypatch.setattr(listener, "_dt_client", None)
    monkeypatch.setattr(listener, "_nudging_client", None)
    monkeypatch.setattr(listener, "AsyncSessionLocal", db_sessionmaker)

    called: list[int] = []

    async def _dispatch(*_a, **_k):
        called.append(1)
        return 0

    monkeypatch.setattr(listener, "dispatch_grid_alerts", _dispatch)

    await on_pipeline_run(Message(run_payload()))

    assert called == []


# ---------------------------------------------------------------------------
# The kill switch
# ---------------------------------------------------------------------------


def test_alerts_are_disabled_by_default(monkeypatch):
    """A fresh deployment is inert until `GRID_ALERTS_ENABLED=true` is set."""
    monkeypatch.delenv("GRID_ALERTS_ENABLED", raising=False)

    assert type(settings)(_env_file=None).grid_alerts_enabled is False


def test_the_switch_reads_grid_alerts_enabled(monkeypatch):
    monkeypatch.setenv("GRID_ALERTS_ENABLED", "true")

    assert type(settings)(_env_file=None).grid_alerts_enabled is True


async def test_a_completed_grid_run_does_not_dispatch_when_alerts_are_disabled(
    dispatched, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "grid_alerts_enabled", False)

    with caplog.at_level("INFO", logger=listener.logger.name):
        await on_pipeline_run(Message(run_payload()))

    assert dispatched == []
    messages = [r.getMessage() for r in caplog.records if "GRID_ALERTS_ENABLED" in r.getMessage()]
    assert len(messages) == 1
    assert settings.grid_pipeline_flow in messages[0]


async def test_a_disabled_switch_is_not_logged_for_runs_that_would_be_ignored(
    dispatched, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "grid_alerts_enabled", False)

    with caplog.at_level("INFO", logger=listener.logger.name):
        await on_pipeline_run(Message(run_payload(flow="some-other-flow")))
        await on_pipeline_run(Message(run_payload(status="failed")))

    assert not [r for r in caplog.records if "GRID_ALERTS_ENABLED" in r.getMessage()]


# ---------------------------------------------------------------------------
# Reading the window out of the payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-15", "2026-08-15"),
        ("  2026-08-15  ", "2026-08-15"),
        ("2026-08-15T06:30:00Z", "2026-08-15"),
        ("2026-08-15T06:30:00+02:00", "2026-08-15"),
        ("15/08/2026", None),
        ("", None),
        (None, None),
        (20260815, None),
    ],
    ids=["date", "padded", "zulu", "offset", "european", "empty", "none", "int"],
)
# @verifies REQ-0037
def test_a_period_is_a_date_or_the_date_part_of_a_timestamp(value, expected):
    """
    `Z` is rewritten to `+00:00` because `datetime.fromisoformat` rejected it before
    Python 3.11 and the platform still emits it.
    """
    assert _normalise_period(value) == expected


# @verifies REQ-0037
def test_the_horizon_starts_at_the_run_date():
    assert _horizon_dates("2026-08-15T23:30:00+02:00", 3) == [
        "2026-08-15",
        "2026-08-16",
        "2026-08-17",
    ]
    assert _horizon_dates("2026-08-15", 1) == ["2026-08-15"]


# @verifies REQ-0037
def test_an_unreadable_timestamp_falls_back_to_today():
    assert _horizon_dates("not a date", 2)[0] == datetime.now(timezone.utc).date().isoformat()
    assert len(_horizon_dates(None, 2)) == 2


# @verifies REQ-0037
def test_a_datetime_object_is_not_a_period():
    """
    `PipelineRunEvent.timestamp` is a `str`, and `_normalise_period` handles only
    strings — a `datetime` reaching it yields `None`, and the horizon then starts today
    rather than on the run's date. Pinned because the field is one SDK release away from
    being typed as a datetime (`created` beside it already is). See
    `.agents/knowledge/faking-the-sdk-boundary.md`.
    """
    assert _normalise_period(datetime(2026, 8, 15, 6, 30, tzinfo=timezone.utc)) is None
    assert PipelineRunEvent.model_fields["timestamp"].annotation is str
