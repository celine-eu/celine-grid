"""Pydantic schemas for celine-grid API."""

import uuid
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------

class MeUser(BaseModel):
    sub: str
    email: str
    name: Optional[str] = None
    preferred_username: Optional[str] = None
    locale: Optional[str] = None
    network_id: str       # Keycloak org alias of the user's DSO organisation
    organization: str     # Same as network_id; exposed for display use


class MeResponse(BaseModel):
    user: MeUser


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------

class AlertRuleCreate(BaseModel):
    risk_type: Literal["wind", "heat"]
    threshold: Literal["ALERT", "WARNING"]
    operational_unit: Optional[str] = None
    recipients: Optional[str] = None
    active: bool = True


class AlertRuleUpdate(BaseModel):
    risk_type: Optional[Literal["wind", "heat"]] = None
    threshold: Optional[Literal["ALERT", "WARNING"]] = None
    operational_unit: Optional[str] = None
    recipients: Optional[str] = None
    active: Optional[bool] = None


class AlertRuleResponse(BaseModel):
    id: uuid.UUID
    user_id: str
    risk_type: str
    threshold: str
    operational_unit: Optional[str]
    recipients: Optional[str]
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Notification settings
# ---------------------------------------------------------------------------

class NotificationSettingsUpdate(BaseModel):
    email_recipients: Optional[str] = None
    webhook_url: Optional[str] = None


class NotificationSettingsResponse(BaseModel):
    user_id: str
    email_recipients: Optional[str]
    webhook_url: Optional[str]
    updated_at: datetime

    model_config = {"from_attributes": True}
