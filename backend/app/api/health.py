"""Health check."""

from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.deps import SettingsDep
from app.api.schemas import HealthOut

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(settings: SettingsDep) -> HealthOut:
    return HealthOut(status="ok", mode=settings.mode, time=datetime.now(UTC))
