"""User / me endpoint."""

from fastapi import APIRouter

from celine.grid.api.deps import UserDep, resolve_dso_network
from celine.grid.api.schemas import MeResponse, MeUser

router = APIRouter(prefix="/api", tags=["user"])


@router.get("/me", response_model=MeResponse)
async def me(user: UserDep) -> MeResponse:
    """Return the authenticated user's identity claims.

    Raises 403 if the user is not a member of a DSO organisation.
    The network_id is derived from the Keycloak org alias — no mapping needed.
    """

    print(f"{user.claims}")

    network_id = resolve_dso_network(user)
    return MeResponse(
        user=MeUser(
            sub=user.sub,
            email=user.email,
            name=getattr(user, "name", None),
            preferred_username=getattr(user, "preferred_username", None),
            locale=getattr(user, "locale", None),
            network_id=network_id,
            organization=network_id,
        )
    )
