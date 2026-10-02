"""Grid alert dispatcher.

Triggered after a grid-resilience-flow pipeline completes.

For the given network_id and forecast dates:
  1. Load the active AlertRules for that network_id (none → nothing else happens).
  2. Fetch the `risk_km` tratta rows for the dates from the DT (one generic values
     call, so the service stays on the SDK it is pinned to).
  3. For each rule, decide whether any operational unit has km at or above the
     rule's threshold for a hazard the rule names.
  4. Merge the triggered rules per recipient list and send ONE `grid_risk_report`
     nudging event per list — nudging-tool de-duplicates per day on (rule, user),
     which would otherwise collapse a wind rule and a heat rule to the same inbox.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from celine.sdk.dt.client import DTClient
from celine.sdk.nudging.client import NudgingAdminClient
from celine.sdk.openapi.nudging.models import DigitalTwinEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.grid.db.models import AlertRule, NotificationSettings
from celine.grid.services.notification_recipients import (
    parse_recipients,
    synthetic_email_user_id,
)
from celine.grid.services.risk_summary import build_days, triggered_vectors
from celine.grid.settings import settings

logger = logging.getLogger(__name__)

RISK_KM_LIMIT = 20000
TOP_LINES = 15


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


def _merge_by_recipients(
    triggered: list[tuple[AlertRule, list[str], list[str]]],
) -> list[dict[str, Any]]:
    """One group per distinct recipient list (or per operator when there is none)."""
    groups: dict[str, dict[str, Any]] = {}
    for rule, vectors, recipients in triggered:
        user_id = synthetic_email_user_id(recipients) if recipients else rule.user_id
        group = groups.setdefault(
            user_id,
            {"user_id": user_id, "recipients": recipients, "vectors": set(), "threshold": "ALERT", "rules": []},
        )
        group["vectors"].update(vectors)
        if rule.threshold == "WARNING":
            group["threshold"] = "WARNING"
        group["rules"].append(rule)
    return list(groups.values())


def _build_report_payload(
    network_id: str,
    group: dict[str, Any],
    rows: list[dict[str, Any]],
    dates: list[str],
) -> dict[str, Any]:
    vectors = sorted(group["vectors"])
    return {
        "event_type": "grid_risk_report",
        "user_id": group["user_id"],
        "facts": {
            "facts_version": "1.0",
            "scenario": "grid_risk_report",
            "period": dates[0],
            "dates": list(dates),
            "network_id": network_id,
            "threshold": group["threshold"],
            "risk_types": vectors,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "app_url": settings.public_app_url,
            "email_recipients": list(group["recipients"]),
            "days": build_days(rows, dates, vectors, threshold=group["threshold"], top_n=TOP_LINES),
        },
    }


async def dispatch_grid_alerts(
    network_id: str,
    dt: DTClient,
    nudging: NudgingAdminClient,
    session: AsyncSession,
    *,
    dates: list[str],
) -> int:
    """Evaluate active alert rules for *network_id* over *dates* and dispatch reports.

    Returns the number of nudges sent.
    """
    if not dates:
        logger.warning("No forecast dates for network=%s; skipping grid alert dispatch", network_id)
        return 0
    if not network_id:
        # Rules backfilled by migration 002 carry '' — they name no network and never fire.
        return 0

    # ------------------------------------------------------------------
    # 1. Active rules for this network — nothing to evaluate without them
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
    # 2. The exposure table at tratta grain, for every date of the horizon
    # ------------------------------------------------------------------
    try:
        fetched = await dt.grid.fetch_values(
            network_id, "risk_km", {"dates": dates, "level": "tratta"}, limit=RISK_KM_LIMIT
        )
        rows: list[dict[str, Any]] = list(fetched.to_dict().get("items") or [])
    except Exception as exc:
        logger.warning("Failed to fetch risk_km for %s: %s", network_id, exc)
        return 0

    if not rows:
        logger.debug("No risk_km rows for network=%s dates=%s; skipping dispatch", network_id, dates)
        return 0

    # ------------------------------------------------------------------
    # 3. Which rules trigger, and for which hazards
    # ------------------------------------------------------------------
    triggered: list[tuple[AlertRule, list[str], list[str]]] = []
    for rule in rules:
        vectors = triggered_vectors(rows, rule.threshold, list(rule.risk_types or []))
        if not vectors:
            continue
        recipients = await _rule_email_recipients(rule, session)
        triggered.append((rule, vectors, recipients))

    # ------------------------------------------------------------------
    # 4. One report per recipient list
    # ------------------------------------------------------------------
    sent = 0
    for group in _merge_by_recipients(triggered):
        payload = _build_report_payload(network_id, group, rows, dates)
        try:
            await nudging.ingest_event(DigitalTwinEvent.from_dict(payload))
            sent += 1
            logger.debug(
                "Sent grid_risk_report to user=%s network=%s hazards=%s threshold=%s",
                group["user_id"],
                network_id,
                sorted(group["vectors"]),
                group["threshold"],
            )
        except Exception as exc:
            logger.warning("Failed to send grid_risk_report to user=%s: %s", group["user_id"], exc)

    return sent
