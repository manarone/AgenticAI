from fastapi import APIRouter

from agenticai.api.routes.system import router as system_router
from agenticai.api.routes.telegram import router as telegram_router
from agenticai.api.routes.v1 import router as v1_router

api_router = APIRouter()
api_router.include_router(system_router)
api_router.include_router(telegram_router)
api_router.include_router(v1_router)
