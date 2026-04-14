"""MQTT pipeline-run event listener for celine-grid.

Subscribes to celine/pipelines/runs/+ and dispatches grid alerts when the
grid-resilience-flow pipeline completes for any network.

Pattern mirrors flexibility-api/services/pipeline_listener.py.
"""

from __future__ import annotations

import logging

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
    logger.debug("Grid resilience pipeline completed for network=%s", network_id)

    if _dt_client is None or _nudging_client is None:
        logger.warning("Service clients not initialised; skipping alert dispatch")
        return

    async with AsyncSessionLocal() as session:
        count = await dispatch_grid_alerts(network_id, _dt_client, _nudging_client, session)

    if count:
        logger.info(
            "Dispatched %d grid alert nudge(s) for network=%s", count, network_id
        )
