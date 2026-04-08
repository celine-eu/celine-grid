from fastapi import APIRouter
from celine.grid.api import user_router, alerts_router, grid_router


def create_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(user_router)
    router.include_router(alerts_router)
    router.include_router(grid_router)
    return router
