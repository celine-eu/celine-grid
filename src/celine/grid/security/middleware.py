"""Thin security middleware for celine-grid.

Responsibilities:
  - Let public / health paths through without auth.
  - Reject any other request that carries no recognisable token with 401
    before it reaches a route handler (avoids leaking route existence).

Fine-grained scope and network-ownership checks are enforced at the
dependency / endpoint level (see api/deps.py), not here.
"""
from __future__ import annotations

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from celine.grid.settings import settings

logger = logging.getLogger(__name__)

# Paths that require no authentication at all
_PUBLIC = frozenset(
    {
        "/health",
        "/api/docs",
        "/api/redoc",
        "/api/openapi.json",
    }
)


def _has_token(request: Request) -> bool:
    """Mirror `api/deps.py::_extract_token` — the same two headers, in the same order.

    The header name must come from settings here as it does there. This gate runs
    before routing, so a middleware that only knew the default name would refuse every
    request the moment `JWT_HEADER_NAME` was changed, without the dependency that
    honours the setting ever running.
    """
    if request.headers.get(settings.jwt_header_name):
        return True
    auth = request.headers.get("authorization", "")
    return auth.lower().startswith("bearer ")


class PolicyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in _PUBLIC:
            return await call_next(request)

        if not _has_token(request):
            logger.warning(
                "Unauthenticated request: %s %s from %s",
                request.method,
                path,
                request.client.host if request.client else "unknown",
            )
            return JSONResponse({"detail": "Missing authentication token"}, status_code=401)

        return await call_next(request)
