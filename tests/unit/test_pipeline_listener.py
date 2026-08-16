"""What arrives on `celine/pipelines/runs/+`, and what this service does about it.

This is the only entry point to the service that is not an HTTP request, and the only
one with no caller to report an error to: everything it rejects, it rejects silently
into the log.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from celine.sdk.broker import PipelineRunEvent

from celine.grid.services import pipeline_listener as listener
from celine.grid.services.pipeline_listener import (
    _normalise_period,
    _normalise_time,
    _pipeline_nudging_window,
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
async def test_the_namespace_is_used_as_the_network_id(dispatched):
    """
    Third place the same unmapped identifier appears — Keycloak org alias, DT
    `network_id`, Prefect namespace are all one string with no translation anywhere.
    """
    await on_pipeline_run(Message(run_payload(namespace="other-dso")))

    assert dispatched[0]["network_id"] == "other-dso"


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("06:00", "06:00"),
        ("23:59", "23:59"),
        ("2026-08-15T06:30:00Z", "06:30"),
        ("24:00", None),
        ("6:00", None),
        ("06:00:00", None),
        (None, None),
    ],
    ids=["hhmm", "late", "timestamp", "hour-24", "unpadded", "seconds", "none"],
)
# @verifies REQ-0037
def test_a_window_bound_is_a_zero_padded_hh_mm(value, expected):
    """
    `"6:00"` and `"06:00:00"` both fail the pattern *and* fail to parse as a datetime,
    so they become `None` — which cancels the whole dispatch. A pipeline emitting either
    form stops alerting silently.
    """
    assert _normalise_time(value) == expected


# @verifies REQ-0037
def test_the_window_is_found_inside_any_of_the_known_containers():
    """
    Pipelines put their metadata in one of six differently-named dicts depending on
    which repository wrote them. All six are searched, top level first.
    """
    event = PipelineRunEvent.model_validate(run_payload())

    for container in ("facts", "payload", "metadata", "parameters", "params", "data"):
        payload = run_payload(
            **{container: {"period": "2026-08-14", "window_start": "07:00", "window_end": "19:00"}}
        )
        assert _pipeline_nudging_window(payload, event) == (
            "2026-08-14",
            "07:00",
            "19:00",
        )


# @verifies REQ-0037
def test_a_top_level_value_beats_a_nested_one():
    event = PipelineRunEvent.model_validate(run_payload())
    payload = run_payload(period="2026-08-14", facts={"period": "2020-01-01"})

    assert _pipeline_nudging_window(payload, event)[0] == "2026-08-14"


# @verifies REQ-0037
def test_the_period_falls_back_to_the_event_timestamp():
    """
    The window bounds have no such fallback, so a payload carrying no window at all
    still cancels dispatch — the period alone is not enough to describe one.
    """
    payload = run_payload()
    event = PipelineRunEvent.model_validate(payload)

    period, start, end = _pipeline_nudging_window(payload, event)

    assert period == "2026-08-15"
    assert (start, end) == (None, None)


# @verifies REQ-0037
def test_a_datetime_object_is_not_a_period():
    """
    `PipelineRunEvent.timestamp` is a `str`, and `_normalise_period` handles only
    strings — a `datetime` reaching it yields `None` and cancels the dispatch. Pinned
    because the field is one SDK release away from being typed as a datetime (`created`
    beside it already is), and that release would silently stop all alerting. See
    `.agents/knowledge/faking-the-sdk-boundary.md`.
    """
    assert _normalise_period(datetime(2026, 8, 15, 6, 30, tzinfo=timezone.utc)) is None
    assert PipelineRunEvent.model_fields["timestamp"].annotation is str
