"""MQTT pipeline-run event listener for celine-grid.

Subscribes to celine/pipelines/runs/+ and dispatches grid alerts when the
grid-resilience-flow pipeline completes. The flow is published under the pipeline's
own namespace (`grid`), so the networks to evaluate are read from the alert rules;
a run published under any other namespace is taken as that network alone.

Pattern mirrors flexibility-api/services/pipeline_listener.py.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from celine.sdk.auth import OidcClientCredentialsProvider
from celine.sdk.broker import MqttBroker, MqttConfig, PipelineRunEvent, ReceivedMessage
from celine.sdk.dt.client import DTClient
from celine.sdk.nudging.client import NudgingAdminClient
from sqlalchemy import select

from celine.grid.db.models import AlertRule
from celine.grid.db.session import AsyncSessionLocal
from celine.grid.services.alert_dispatcher import dispatch_grid_alerts
from celine.grid.settings import settings

logger = logging.getLogger(__name__)

_broker: MqttBroker | None = None
_dt_client: DTClient | None = None
_nudging_client: NudgingAdminClient | None = None
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


def _horizon_dates(timestamp: Any, days: int) -> list[str]:
    """The forecast dates a run evaluates: its own date, then `days - 1` more.

    Falls back to today when the timestamp cannot be read — the horizon must never be
    empty, or every run would silently dispatch nothing.
    """
    period = _normalise_period(timestamp)
    start = date.fromisoformat(period) if period else datetime.now(timezone.utc).date()
    return [(start + timedelta(days=i)).isoformat() for i in range(max(days, 1))]


def _is_pipeline_namespace(namespace: str) -> bool:
    own = settings.grid_pipeline_namespace
    return namespace == own or namespace.endswith("." + own)


async def _networks_with_active_rules(session: Any) -> list[str]:
    result = await session.execute(
        select(AlertRule.network_id)
        .where(AlertRule.active.is_(True), AlertRule.network_id != "")
        .distinct()
    )
    return sorted(str(n) for n in result.scalars().all())


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

    if not settings.grid_alerts_enabled:
        logger.info(
            "Grid alerts disabled (GRID_ALERTS_ENABLED=false); skipping dispatch for flow=%s namespace=%s",
            event.flow,
            event.namespace,
        )
        return

    dates = _horizon_dates(event.timestamp, settings.grid_alert_horizon_days)
    logger.debug(
        "Grid resilience pipeline completed namespace=%s dates=%s", event.namespace, dates
    )

    if _dt_client is None or _nudging_client is None:
        logger.warning("Service clients not initialised; skipping alert dispatch")
        return

    async with AsyncSessionLocal() as session:
        if _is_pipeline_namespace(event.namespace):
            networks = await _networks_with_active_rules(session)
        else:
            networks = [event.namespace]

        for network_id in networks:
            count = await dispatch_grid_alerts(
                network_id,
                _dt_client,
                _nudging_client,
                session,
                dates=dates,
            )
            if count:
                logger.info(
                    "Dispatched %d grid alert nudge(s) for network=%s", count, network_id
                )
