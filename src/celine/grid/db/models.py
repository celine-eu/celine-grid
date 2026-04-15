"""SQLAlchemy database models for celine-grid."""

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import JSON, String, Boolean, DateTime, Integer, Uuid, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class AlertRule(Base):
    """Grid alert rules configured by operators."""

    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    network_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    risk_types: Mapped[list] = mapped_column(JSON, nullable=False)  # ["wind"], ["heat"], or ["wind","heat"]
    threshold: Mapped[str] = mapped_column(String(20), nullable=False)  # ALERT | WARNING
    network_id_unset: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # True for rows backfilled by 002 with network_id=''
    recipients: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotificationSettings(Base):
    """Per-user global notification settings."""

    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    email_recipients: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    webhook_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
