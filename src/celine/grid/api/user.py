"""User / me endpoint."""

from fastapi import APIRouter

from celine.grid.api.deps import UserDep
from celine.grid.api.schemas import MeResponse, MeUser

router = APIRouter(prefix="/api", tags=["user"])


@router.get("/me", response_model=MeResponse)
async def me(user: UserDep) -> MeResponse:
    """Return the authenticated user's identity claims."""
    return MeResponse(
        user=MeUser(
            sub=user.sub,
            email=user.email,
            name=getattr(user, "name", None),
            preferred_username=getattr(user, "preferred_username", None),
            locale=getattr(user, "locale", None),
        )
    )
