"""Recipient helpers for Grid-triggered nudging notifications."""

from __future__ import annotations

import hashlib
import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _dedupe_emails(values: list[str]) -> list[str]:
    recipients: list[str] = []
    seen: set[str] = set()
    for item in values:
        email = item.strip()
        if not email or not _EMAIL_RE.match(email):
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        recipients.append(email)
    return recipients


def parse_recipients(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,;\s]+", value)
    return _dedupe_emails(parts)


def synthetic_email_user_id(email_recipients: list[str]) -> str:
    digest = hashlib.sha256(
        "|".join(sorted(email.lower() for email in email_recipients)).encode("utf-8")
    ).hexdigest()[:16]
    return f"email-ingest:{digest}"
