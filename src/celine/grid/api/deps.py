"""Authentication and service dependencies."""

import logging
from typing import Annotated

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from celine.grid.db import get_db
from celine.grid.settings import settings
from celine.sdk.auth import JwtUser
from celine.sdk.auth.static import StaticTokenProvider
from celine.sdk.dt import DTClient

logger = logging.getLogger(__name__)


def _extract_token(request: Request) -> str | None:
    token = request.headers.get(settings.jwt_header_name)
    if token:
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    logger.warning("Missing auth headers on %s %s", request.method, request.url.path)
    return None


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


def get_raw_token(request: Request) -> str:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    return token


def get_dt_client(request: Request) -> DTClient:
    if not settings.digital_twin_api_url:
        raise HTTPException(status_code=503, detail="Digital Twin API not configured")
    raw_token = get_raw_token(request)
    return DTClient(
        base_url=settings.digital_twin_api_url,
        token_provider=StaticTokenProvider(raw_token),
    )


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


UserDep = Annotated[JwtUser, Depends(get_user_from_request)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
DTDep = Annotated[DTClient, Depends(get_dt_client)]

# ---------------------------------------------------------------------------
# Organisation / network helpers
# ---------------------------------------------------------------------------

DSO_TYPE = "dso"


def resolve_dso_network(user: JwtUser) -> str:
    """Return the network_id for the user's DSO organisation.

    The Keycloak org alias is used directly as the network_id — no mapping
    table needed. Operators configure their KC org alias to match their DT
    network ID at deployment time.

    The org attribute ``type=dso`` (set by celine-policies sync-orgs) is used
    to identify DSO organisations in the token.

    Raises HTTP 403 if the user is not a member of any DSO organisation.
    """
    for org in user.organizations:
        if org.type == DSO_TYPE:
            return org.alias
    raise HTTPException(status_code=403, detail="DSO organisation membership required")
