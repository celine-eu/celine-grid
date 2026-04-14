"""Pydantic schemas for celine-grid API."""

import uuid
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, field_validator


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
    risk_types: list[Literal["wind", "heat"]]
    threshold: Literal["ALERT", "WARNING"]
    recipients: Optional[str] = None
    active: bool = True

    @field_validator("risk_types")
    @classmethod
    def non_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("risk_types must contain at least one value")
        return v


class AlertRuleUpdate(BaseModel):
    risk_types: Optional[list[Literal["wind", "heat"]]] = None
    threshold: Optional[Literal["ALERT", "WARNING"]] = None
    recipients: Optional[str] = None
    active: Optional[bool] = None

    @field_validator("risk_types")
    @classmethod
    def non_empty(cls, v: Optional[list]) -> Optional[list]:
        if v is not None and not v:
            raise ValueError("risk_types must contain at least one value")
        return v


class AlertRuleResponse(BaseModel):
    id: uuid.UUID
    user_id: str
    network_id: str
    risk_types: list[str]
    threshold: str
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
