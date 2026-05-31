from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_role
from app.db.session import get_session
from app.models.user import User
from app.services.geo_service import (
    calculate_coverage_gaps,
    calculate_existing_points_500m,
    calculate_is_suitable,
)

router = APIRouter()


@router.post("/recalculate")
async def geo_recalculate(
    threshold_m: int = 200,
    _: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """
    admin — ejecuta el pipeline PostGIS completo tras un commit de recycling_points:
    1. calculate_is_suitable  → etiqueta candidate_zones con is_suitable 1/0
    2. calculate_coverage_gaps → actualiza coverage_gap_m y dist_to_nearest_point_m
    3. calculate_existing_points_500m → actualiza existing_points_500m
    """
    is_suitable_result = await calculate_is_suitable(db, threshold_m=threshold_m)
    coverage_result = await calculate_coverage_gaps(db)
    points_500m_result = await calculate_existing_points_500m(db)

    return {
        "is_suitable": is_suitable_result,
        "coverage_gaps": coverage_result,
        "existing_points_500m": points_500m_result,
    }
