from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.session import get_session
from app.models.user import User
from app.services.geo_service import (
    get_districts_geojson,
    get_nearby_points_geojson,
    get_point_by_id_geojson,
    get_points_geojson,
)

router = APIRouter()
_GEO = "application/geo+json"


@router.get("/points/nearby")
async def map_points_nearby(
    lat: float,
    lon: float,
    radius_m: int = 1000,
    db: AsyncSession = Depends(get_session),
):
    if radius_m > 5000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El radio máximo permitido es 5000m.",
        )
    data = await get_nearby_points_geojson(db, lat=lat, lon=lon, radius_m=radius_m)
    return JSONResponse(content=data, media_type=_GEO)


@router.get("/points/{point_id}")
async def map_point_by_id(
    point_id: int,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    data = await get_point_by_id_geojson(db, point_id=point_id)
    return JSONResponse(content=data, media_type=_GEO)


@router.get("/points")
async def map_points(
    district_id: int | None = None,
    material: str | None = None,
    verified_only: bool = False,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    data = await get_points_geojson(
        db, district_id=district_id, material=material, verified_only=verified_only
    )
    return JSONResponse(content=data, media_type=_GEO)


@router.get("/districts")
async def map_districts(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    data = await get_districts_geojson(db)
    return JSONResponse(content=data, media_type=_GEO)
