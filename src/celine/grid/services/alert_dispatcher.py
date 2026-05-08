"""Grid alert dispatcher.

Triggered after a grid-resilience-flow pipeline completes.

For the given network_id:
  1. Fetch wind and heat alert distributions from the DT.
  2. Load all active AlertRules for that network_id from the DB.
  3. For each rule, check whether the current risk distribution meets or
     exceeds the rule's threshold:
       - threshold=ALERT  → triggered when ALERT-level events exist
       - threshold=WARNING → triggered when WARNING or ALERT-level events exist
  4. Send an extr_event nudging event to the rule's recipients for every triggered rule.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.sdk.dt.client import DTClient
from celine.sdk.nudging.client import NudgingAdminClient
from celine.sdk.openapi.nudging.models import DigitalTwinEvent

from celine.grid.db.models import AlertRule, NotificationSettings
from celine.grid.services.notification_recipients import (
    parse_recipients,
    synthetic_email_user_id,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Risk level ordering — higher index = higher severity
_RISK_LEVELS = ["LOW", "WARNING", "ALERT"]
_THRESHOLD_FLOOR: dict[str, set[str]] = {
    "WARNING": {"WARNING", "ALERT"},
    "ALERT": {"ALERT"},
}
def _has_events(distribution: list[dict], risk_levels: set[str]) -> bool:
    """Return True if any matching risk level has at least one event."""
    for item in distribution:
        level = (item.get("risk_level") or "").upper()
        if level in risk_levels and (item.get("events") or 0) > 0:
            return True
    return False


async def _rule_email_recipients(rule: AlertRule, session: AsyncSession) -> list[str]:
    recipients = parse_recipients(rule.recipients)
    if recipients:
        return recipients

    result = await session.execute(
        select(NotificationSettings.email_recipients).where(
            NotificationSettings.user_id == rule.user_id
        )
    )
    return parse_recipients(result.scalar_one_or_none())


def _event_type_from_triggered_types(triggered_types: list[str]) -> str:
    if "heat" in triggered_types and "wind" not in triggered_types:
        return "heat"
    if "wind" in triggered_types and "heat" not in triggered_types:
        return "wind"
    return "thunderstorm"


async def _build_nudging_payload(
    rule: AlertRule,
    *,
    triggered_types: list[str],
    period: str,
    window_start: str,
    window_end: str,
    session: AsyncSession,
) -> dict:
    recipients = await _rule_email_recipients(rule, session)
    user_id = synthetic_email_user_id(recipients) if recipients else rule.user_id
    return {
        "event_type": "extr_event",
        "user_id": user_id,
        "facts": {
            "facts_version": "1.0",
            "scenario": "extr_event",
            "period": period,
            "window_start": window_start,
            "window_end": window_end,
            "event_type": _event_type_from_triggered_types(triggered_types),
            "day_label": "today",
            "email_recipients": recipients,
        },
    }


async def dispatch_grid_alerts(
    network_id: str,
    dt: DTClient,
    nudging: NudgingAdminClient,
    session: AsyncSession,
    *,
    period: str | None,
    window_start: str | None,
    window_end: str | None,
) -> int:
    """Evaluate active alert rules for *network_id* and dispatch nudges.

    Returns the number of nudges sent.
    """
    if not period or not window_start or not window_end:
        logger.warning(
            "Pipeline window metadata missing for network=%s; skipping grid alert dispatch",
            network_id,
        )
        return 0

    # ------------------------------------------------------------------
    # 1. Fetch current distributions from the DT (one call per hazard type)
    # ------------------------------------------------------------------
    wind_dist: list[dict] = []
    heat_dist: list[dict] = []

    try:
        wind_dist = await dt.grid.wind_alert_distribution(network_id)
    except Exception as exc:
        logger.warning("Failed to fetch wind_alert_distribution for %s: %s", network_id, exc)

    try:
        heat_dist = await dt.grid.heat_alert_distribution(network_id)
    except Exception as exc:
        logger.warning("Failed to fetch heat_alert_distribution for %s: %s", network_id, exc)

    if not wind_dist and not heat_dist:
        logger.debug("No distribution data available for network=%s; skipping dispatch", network_id)
        return 0

    # ------------------------------------------------------------------
    # 2. Load active rules for this network
    # ------------------------------------------------------------------
    result = await session.execute(
        select(AlertRule).where(
            AlertRule.network_id == network_id,
            AlertRule.active.is_(True),
        )
    )
    rules: list[AlertRule] = list(result.scalars().all())

    if not rules:
        logger.debug("No active alert rules for network=%s", network_id)
        return 0

    # ------------------------------------------------------------------
    # 3 & 4. Evaluate each rule and send nudges
    # ------------------------------------------------------------------
    sent = 0
    for rule in rules:
        threshold_levels = _THRESHOLD_FLOOR.get(rule.threshold, {"ALERT"})
        triggered_types: list[str] = []

        if "wind" in rule.risk_types and _has_events(wind_dist, threshold_levels):
            triggered_types.append("wind")
        if "heat" in rule.risk_types and _has_events(heat_dist, threshold_levels):
            triggered_types.append("heat")

        if not triggered_types:
            continue

        payload = await _build_nudging_payload(
            rule,
            triggered_types=triggered_types,
            period=period,
            window_start=window_start,
            window_end=window_end,
            session=session,
        )
        try:
            await nudging.ingest_event(DigitalTwinEvent.from_dict(payload))
            sent += 1
            logger.debug(
                "Sent %s nudge to user=%s network=%s types=%s threshold=%s",
                payload["event_type"],
                rule.user_id,
                network_id,
                triggered_types,
                rule.threshold,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send %s nudge to user=%s: %s",
                payload["event_type"],
                rule.user_id,
                exc,
            )

    return sent
