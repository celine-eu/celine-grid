"""MQTT pipeline-run event listener for celine-grid.

Subscribes to celine/pipelines/runs/+ and dispatches grid alerts when the
grid-resilience-flow pipeline completes for any network.

Pattern mirrors flexibility-api/services/pipeline_listener.py.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from celine.sdk.auth import OidcClientCredentialsProvider
from celine.sdk.broker import MqttBroker, MqttConfig, PipelineRunEvent, ReceivedMessage
from celine.sdk.dt.client import DTClient
from celine.sdk.nudging.client import NudgingAdminClient

from celine.grid.settings import settings
from celine.grid.db.session import AsyncSessionLocal
from celine.grid.services.alert_dispatcher import dispatch_grid_alerts

logger = logging.getLogger(__name__)

_broker: MqttBroker | None = None
_dt_client: DTClient | None = None
_nudging_client: NudgingAdminClient | None = None
_METADATA_CONTAINERS = ("facts", "payload", "metadata", "parameters", "params", "data")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def get_broker() -> MqttBroker | None:
    return _broker


def _make_oidc_provider(scope: str | None) -> OidcClientCredentialsProvider:
    return OidcClientCredentialsProvider(
        base_url=settings.oidc.base_url,
        client_id=settings.oidc.client_id or "",
        client_secret=settings.oidc.client_secret or "",
        scope=scope,
    )


def create_broker() -> MqttBroker:
    """Create service clients and the MQTT broker (call once at startup)."""
    global _broker, _dt_client, _nudging_client

    if settings.digital_twin_api_url:
        _dt_client = DTClient(
            base_url=settings.digital_twin_api_url,
            token_provider=_make_oidc_provider(settings.dt_client_scope),
        )

    _nudging_client = NudgingAdminClient(
        base_url=settings.nudging_api_url,
        token_provider=_make_oidc_provider(settings.nudging_scope),
    )

    cfg = MqttConfig(
        host=settings.mqtt.host,
        port=settings.mqtt.port,
        username=settings.mqtt.username,
        password=settings.mqtt.password,
        use_tls=settings.mqtt.use_tls,
        ca_certs=settings.mqtt.ca_certs,
        keepalive=settings.mqtt.keepalive,
        clean_session=settings.mqtt.clean_session,
        reconnect_interval=settings.mqtt.reconnect_interval,
        max_reconnect_attempts=settings.mqtt.max_reconnect_attempts,
        client_id=settings.mqtt.client_id,
        topic_prefix=settings.mqtt.topic_prefix,
    )
    mqtt_token_provider = _make_oidc_provider(None)
    _broker = MqttBroker(cfg, token_provider=mqtt_token_provider)
    return _broker


def _find_pipeline_value(payload: dict[str, Any], key: str) -> Any:
    if key in payload:
        return payload[key]

    for container in _METADATA_CONTAINERS:
        nested = payload.get(container)
        if isinstance(nested, dict) and key in nested:
            return nested[key]

    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _normalise_period(value: Any) -> str | None:
    if isinstance(value, str) and _DATE_RE.match(value.strip()):
        return value.strip()

    dt = _parse_datetime(value)
    if dt:
        return dt.date().isoformat()

    return None


def _normalise_time(value: Any) -> str | None:
    if isinstance(value, str):
        raw = value.strip()
        if _TIME_RE.match(raw):
            return raw

    dt = _parse_datetime(value)
    if dt:
        return dt.strftime("%H:%M")

    return None


def _pipeline_nudging_window(
    payload: dict[str, Any],
    event: PipelineRunEvent,
) -> tuple[str | None, str | None, str | None]:
    period = _normalise_period(_find_pipeline_value(payload, "period"))
    if not period:
        period = _normalise_period(event.timestamp)

    window_start = _normalise_time(_find_pipeline_value(payload, "window_start"))
    window_end = _normalise_time(_find_pipeline_value(payload, "window_end"))
    return period, window_start, window_end


async def on_pipeline_run(msg: ReceivedMessage) -> None:
    """Handle celine/pipelines/runs/+ messages."""
    try:
        event = PipelineRunEvent.model_validate(msg.payload)
    except Exception as exc:
        logger.warning("Failed to parse PipelineRunEvent: %s", exc)
        return

    if event.status != "completed":
        return

    if event.flow != settings.grid_pipeline_flow:
        return

    network_id = event.namespace
    period, window_start, window_end = _pipeline_nudging_window(msg.payload, event)
    logger.debug(
        "Grid resilience pipeline completed for network=%s period=%s window=%s-%s",
        network_id,
        period,
        window_start,
        window_end,
    )

    if _dt_client is None or _nudging_client is None:
        logger.warning("Service clients not initialised; skipping alert dispatch")
        return

    async with AsyncSessionLocal() as session:
        count = await dispatch_grid_alerts(
            network_id,
            _dt_client,
            _nudging_client,
            session,
            period=period,
            window_start=window_start,
            window_end=window_end,
        )

    if count:
        logger.info(
            "Dispatched %d grid alert nudge(s) for network=%s", count, network_id
        )
