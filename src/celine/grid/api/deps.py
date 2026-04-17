"""Authentication and service dependencies."""

import logging
from typing import Annotated

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from celine.grid.db import get_db
from celine.grid.settings import settings
from celine.grid.security.policy import policy
from celine.sdk.auth import JwtUser, OidcClientCredentialsProvider
from celine.sdk.dt import DTClient

logger = logging.getLogger(__name__)

DSO_TYPE = "dso"


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

def _extract_token(request: Request) -> str | None:
    token = request.headers.get(settings.jwt_header_name)
    if token:
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    logger.warning("Missing auth headers on %s %s", request.method, request.url.path)
    return None


# ---------------------------------------------------------------------------
# Base auth deps
# ---------------------------------------------------------------------------

def get_user_from_request(request: Request) -> JwtUser:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    try:
        return JwtUser.from_token(token, oidc=settings.oidc)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except pyjwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {e}")


def _make_oidc_provider(scope: str | None = None) -> OidcClientCredentialsProvider:
    return OidcClientCredentialsProvider(
        base_url=settings.oidc.base_url,
        client_id=settings.oidc.client_id or "",
        client_secret=settings.oidc.client_secret or "",
        scope=scope,
    )


def get_dt_client(request: Request) -> DTClient:
    if not settings.digital_twin_api_url:
        raise HTTPException(status_code=503, detail="Digital Twin API not configured")
    return DTClient(
        base_url=settings.digital_twin_api_url,
        token_provider=_make_oidc_provider(settings.dt_client_scope),
    )


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Organisation helpers
# ---------------------------------------------------------------------------

def resolve_dso_network(user: JwtUser) -> str:
    """Return the network_id for the user's DSO organisation.

    The Keycloak org alias is used directly as the network_id — no mapping
    table needed.  Raises HTTP 403 if the user has no DSO organisation.

    KC 26 org mapper may emit type either at the top-level org dict or inside
    the attributes map; both locations are checked.
    """
    for org in user.organizations:
        if org.type == DSO_TYPE or org.has_attribute("type", DSO_TYPE):
            return org.alias
    raise HTTPException(status_code=403, detail="DSO organisation membership required")


# ---------------------------------------------------------------------------
# Policy-enforced dependencies
# ---------------------------------------------------------------------------

async def require_network_read(
    network_id: str,  # injected from path by FastAPI
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    """Require that the caller may read DT data for *network_id*.

    - Users: must hold ``grid.read`` / ``grid.admin`` **and** belong to the
      DSO organisation whose alias equals *network_id*.
    - Service accounts: must hold ``grid.read`` / ``grid.admin``; no
      ownership check.
    """
    d = await policy.allow_network_read(user, network_id)
    if not d.allowed:
        logger.warning(
            "403 network read denied: sub=%s network_id=%s reason=%s",
            user.sub, network_id, d.reason,
        )
        raise HTTPException(status_code=403, detail=d.reason or "access denied")
    return user


async def require_alerts_read(
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    """Require ``grid.alerts.read``, ``grid.alerts.write``, or ``grid.admin``."""
    d = await policy.allow_alerts_read(user)
    if not d.allowed:
        logger.warning(
            "403 alerts read denied: sub=%s reason=%s", user.sub, d.reason,
        )
        raise HTTPException(status_code=403, detail=d.reason or "access denied")
    return user


async def require_alerts_write(
    user: Annotated[JwtUser, Depends(get_user_from_request)],
) -> JwtUser:
    """Require ``grid.alerts.write`` or ``grid.admin``."""
    d = await policy.allow_alerts_write(user)
    if not d.allowed:
        logger.warning(
            "403 alerts write denied: sub=%s reason=%s", user.sub, d.reason,
        )
        raise HTTPException(status_code=403, detail=d.reason or "access denied")
    return user


# ---------------------------------------------------------------------------
# Annotated shorthand types
# ---------------------------------------------------------------------------

UserDep = Annotated[JwtUser, Depends(get_user_from_request)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
DTDep = Annotated[DTClient, Depends(get_dt_client)]

# DT proxy — verifies scope + DSO network ownership
NetworkReadDep = Annotated[JwtUser, Depends(require_network_read)]

# Alert rules / notification settings
AlertsReadDep = Annotated[JwtUser, Depends(require_alerts_read)]
AlertsWriteDep = Annotated[JwtUser, Depends(require_alerts_write)]
