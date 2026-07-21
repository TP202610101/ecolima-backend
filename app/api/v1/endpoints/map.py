from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_current_user_optional, require_role
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
    get_saturation_data,
)

router = APIRouter()
_GEO = "application/geo+json"


# ── /points ─── colección; combina listado, filtros y búsqueda por cercanía ──
# ?lat=&lon=(&radius_m=)   -> modo "cercanos", PÚBLICO (sin auth)
# resto de combinaciones   -> Auth (todos), filtros combinables por query params

@router.get("/points/{point_id}")
async def map_point_by_id(
    point_id: int,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Auth (todos) — popup de punto en el mapa."""
    data = await get_point_by_id_geojson(db, point_id=point_id)
    return JSONResponse(content=data, media_type=_GEO)


@router.get("/points")
@limiter.limit("100/minute")
async def map_points(
    request: Request,
    district_id: int | None = None,
    material: str | None = None,
    point_type: str | None = None,
    verified: bool | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_m: int = 1000,
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_session),
):
    """
    Modo "cercanos" — GET /map/points?lat=&lon=(&radius_m=): público, sin
    autenticación, limitado a 100 req/min.

    Resto de combinaciones — Auth (todos). Filtros combinables:
    ?district_id=&material=&point_type=&verified=
    """
    if lat is not None and lon is not None:
        if radius_m > 5000:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El radio máximo permitido es 5000m.",
            )
        data = await get_nearby_points_geojson(db, lat=lat, lon=lon, radius_m=radius_m)
        return JSONResponse(content=data, media_type=_GEO)

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales incorrectas")

    data = await get_filtered_points_geojson(
        db,
        district_id=district_id,
        material=material,
        point_type=point_type,
        verified=verified,
    )
    return JSONResponse(content=data, media_type=_GEO)


# ── /districts/* ───────────────────────────────────────────────────────────────

@router.get("/districts/{district_id}/stats")
async def map_district_stats(
    district_id: int,
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — estadísticas de cobertura por distrito."""
    return await get_district_stats(db, district_id=district_id)


@router.get("/districts")
async def map_districts(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Auth (todos) — mapa base de los 43 distritos."""
    data = await get_districts_geojson(db)
    return JSONResponse(content=data, media_type=_GEO)


# ── Comparación y heatmap ──────────────────────────────────────────────────────

@router.get("/comparison")
async def map_comparison(
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — split-view actual vs recomendado."""
    data = await get_comparison_geojson(db)
    return JSONResponse(content=data, media_type=_GEO)


@router.get("/heatmap")
async def map_heatmap(
    district_id: int | None = None,
    metric: str = "density",
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — heatmap zonas críticas. metric: density|priority|gap"""
    data = await get_heatmap_geojson(db, district_id=district_id, metric=metric)
    return JSONResponse(content=data, media_type=_GEO)


@router.get("/saturation")
async def map_saturation(
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — semáforo de saturación por distrito.
    status: verde 0-50%, amarillo 51-80%, rojo >80%."""
    return await get_saturation_data(db)
