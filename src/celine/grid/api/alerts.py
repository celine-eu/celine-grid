"""Alert rules and notification settings endpoints."""

import uuid
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from celine.grid.api.deps import UserDep, DbDep
from celine.grid.api.schemas import (
    AlertRuleCreate,
    AlertRuleUpdate,
    AlertRuleResponse,
    NotificationSettingsUpdate,
    NotificationSettingsResponse,
)
from celine.grid.db.models import AlertRule, NotificationSettings

router = APIRouter(prefix="/api", tags=["alerts"])


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------

@router.get("/alert-rules", response_model=list[AlertRuleResponse])
async def list_alert_rules(user: UserDep, db: DbDep) -> list[AlertRule]:
    result = await db.execute(
        select(AlertRule).where(AlertRule.user_id == user.sub).order_by(AlertRule.created_at)
    )
    return list(result.scalars().all())


@router.post("/alert-rules", response_model=AlertRuleResponse, status_code=201)
async def create_alert_rule(body: AlertRuleCreate, user: UserDep, db: DbDep) -> AlertRule:
    rule = AlertRule(user_id=user.sub, **body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: uuid.UUID, body: AlertRuleUpdate, user: UserDep, db: DbDep
) -> AlertRule:
    result = await db.execute(
        select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user.sub)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/alert-rules/{rule_id}", status_code=204)
async def delete_alert_rule(rule_id: uuid.UUID, user: UserDep, db: DbDep) -> None:
    result = await db.execute(
        select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user.sub)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    await db.delete(rule)
    await db.commit()


# ---------------------------------------------------------------------------
# Notification settings
# ---------------------------------------------------------------------------

@router.get("/notification-settings", response_model=NotificationSettingsResponse)
async def get_notification_settings(user: UserDep, db: DbDep) -> NotificationSettings:
    result = await db.execute(
        select(NotificationSettings).where(NotificationSettings.user_id == user.sub)
    )
    ns = result.scalar_one_or_none()
    if not ns:
        ns = NotificationSettings(user_id=user.sub)
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
    return ns


@router.put("/notification-settings", response_model=NotificationSettingsResponse)
async def update_notification_settings(
    body: NotificationSettingsUpdate, user: UserDep, db: DbDep
) -> NotificationSettings:
    result = await db.execute(
        select(NotificationSettings).where(NotificationSettings.user_id == user.sub)
    )
    ns = result.scalar_one_or_none()
    if not ns:
        ns = NotificationSettings(user_id=user.sub)
        db.add(ns)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(ns, field, value)
    await db.commit()
    await db.refresh(ns)
    return ns
