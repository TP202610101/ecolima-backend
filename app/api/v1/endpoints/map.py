from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_role
from app.core.limiter import limiter
from app.db.session import get_session
from app.models.user import User
from app.services.geo_service import (
    get_comparison_geojson,
    get_district_stats,
    get_districts_geojson,
    get_filtered_points_geojson,
    get_heatmap_geojson,
    get_nearby_points_geojson,
    get_point_by_id_geojson,
    get_points_geojson,
    get_saturation_data,
)

router = APIRouter()
_GEO = "application/geo+json"


# ── /points/* — literal paths first, then parametrized ────────────────────────

@router.get("/points/nearby")
@limiter.limit("100/minute")
async def map_points_nearby(
    request: Request,
    lat: float,
    lon: float,
    radius_m: int = 1000,
    db: AsyncSession = Depends(get_session),
):
    """Público — sin autenticación (HU-29). Limitado a 100 req/min."""
    if radius_m > 5000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El radio máximo permitido es 5000m.",
        )
    data = await get_nearby_points_geojson(db, lat=lat, lon=lon, radius_m=radius_m)
    return JSONResponse(content=data, media_type=_GEO)


@router.get("/points/filter")
async def map_points_filter(
    district_id: int | None = None,
    material: str | None = None,
    point_type: str | None = None,
    verified: bool | None = None,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Auth (todos) — filtros combinables (HU-22, HU-23)."""
    data = await get_filtered_points_geojson(
        db,
        district_id=district_id,
        material=material,
        point_type=point_type,
        verified=verified,
    )
    return JSONResponse(content=data, media_type=_GEO)


@router.get("/points/{point_id}")
async def map_point_by_id(
    point_id: int,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Auth (todos) — popup Leaflet (HU-21)."""
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
    """Auth (todos) — listado general (HU-20)."""
    data = await get_points_geojson(
        db, district_id=district_id, material=material, verified_only=verified_only
    )
    return JSONResponse(content=data, media_type=_GEO)


# ── /districts/* ───────────────────────────────────────────────────────────────

@router.get("/districts/{district_id}/stats")
async def map_district_stats(
    district_id: int,
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — estadísticas por distrito (HU-19)."""
    return await get_district_stats(db, district_id=district_id)


@router.get("/districts")
async def map_districts(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Auth (todos) — mapa base 43 distritos (HU-19)."""
    data = await get_districts_geojson(db)
    return JSONResponse(content=data, media_type=_GEO)


# ── Comparación y heatmap ──────────────────────────────────────────────────────

@router.get("/comparison")
async def map_comparison(
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — split-view actual vs recomendado (HU-25)."""
    data = await get_comparison_geojson(db)
    return JSONResponse(content=data, media_type=_GEO)


@router.get("/heatmap")
async def map_heatmap(
    district_id: int | None = None,
    metric: str = "density",
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — heatmap zonas críticas (HU-27). metric: density|priority|gap"""
    data = await get_heatmap_geojson(db, district_id=district_id, metric=metric)
    return JSONResponse(content=data, media_type=_GEO)


@router.get("/saturation")
async def map_saturation(
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — semáforo de saturación por distrito (HU-45).
    status: verde 0-50%, amarillo 51-80%, rojo >80%."""
    return await get_saturation_data(db)
