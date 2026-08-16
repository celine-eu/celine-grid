"""User / me endpoint."""

import logging

from fastapi import APIRouter, Request

from celine.grid.api.deps import UserDep, resolve_dso_network
from celine.grid.api.schemas import MeResponse, MeUser
from celine.sdk.auth import JwtUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["user"])


def _primary_subtag(tag: str) -> str | None:
    """Reduce a BCP47 tag to its lowercased primary language subtag.

    `it-IT` -> `it`. The single consumer (`celine-frontend/apps/grid`) matches the value
    against a two-entry list of supported languages, so a region-qualified tag silently
    resolves to English there. Shaping the value for the client that reads it is what a
    BFF is for; a consumer that later needs the region will need this to change, and
    REQ-0044 is where that decision is recorded.
    """
    primary = tag.strip().split("-")[0].lower()
    return primary if primary.isalpha() else None


def _from_accept_language(header: str | None) -> str | None:
    """Highest-priority language from an `Accept-Language` header, or None.

    `it-IT,it;q=0.9,en;q=0.8` -> `it`. Entries are ranked by their `q` value, ties keep
    the order sent, and the `*` wildcard is skipped because it expresses no preference.
    A malformed `q` is treated as the default of 1.0 rather than dropping the entry —
    the header is caller-supplied and this value only picks a language.
    """
    if not header:
        return None

    ranked: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        tag, _, params = part.strip().partition(";")
        tag = tag.strip()
        if not tag or tag == "*":
            continue
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 1.0
        if quality > 0:
            ranked.append((-quality, index, tag))

    for _q, _i, tag in sorted(ranked):
        primary = _primary_subtag(tag)
        if primary:
            return primary
    return None


def resolve_locale(user: JwtUser, request: Request) -> str | None:
    """The caller's preferred language: the token first, then the request.

    `locale` is part of the standard OIDC `profile` scope, so a realm that maps that
    scope supplies it. Not every caller has one — a service account has no profile at
    all — and the browser has already said what it wants in `Accept-Language`, so that
    is the fallback rather than nothing.

    `celine-frontend/apps/grid` continues the chain from there: a `localStorage`
    override wins over this value, and `getLocaleFromNavigator()` backs it up.
    """
    claim = user.claims.get("locale")
    if isinstance(claim, str) and claim.strip():
        primary = _primary_subtag(claim)
        if primary:
            return primary

    return _from_accept_language(request.headers.get("accept-language"))


@router.get("/ping", include_in_schema=False)
async def ping(user: UserDep) -> dict:
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(request: Request, user: UserDep) -> MeResponse:
    """Return the authenticated user's identity claims.

    Raises 403 if the user is not a member of a DSO organisation.
    The network_id is derived from the Keycloak org alias — no mapping needed.
    """

    log.debug("me claims: %s", user.claims)
    network_id = resolve_dso_network(user)
    return MeResponse(
        user=MeUser(
            sub=user.sub,
            email=user.email,
            name=getattr(user, "name", None),
            preferred_username=getattr(user, "preferred_username", None),
            # Out of the claims, not off the dataclass: JwtUser models no `locale`
            # field, so a getattr for it can only ever return the default.
            locale=resolve_locale(user, request),
            network_id=network_id,
            organization=network_id,
        )
    )
