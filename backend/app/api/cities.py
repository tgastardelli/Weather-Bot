"""City registry endpoints."""

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import SessionDep
from app.api.schemas import CityOut
from app.db.models import City

router = APIRouter(prefix="/api/cities", tags=["cities"])


@router.get("")
async def list_cities(session: SessionDep) -> list[CityOut]:
    rows = (
        (
            await session.execute(
                select(City).order_by(City.active.desc(), City.needs_review, City.slug)
            )
        )
        .scalars()
        .all()
    )
    return [CityOut.model_validate(row) for row in rows]
