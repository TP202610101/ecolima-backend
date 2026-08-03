"""
Endpoints públicos, SIN autenticación.

Junto con el modo "cercanos" de GET /map/points (?lat=&lon=), esta es la
ÚNICA superficie de la API alcanzable sin JWT -- la de escritura-cero para
la vista ciudadano. Se trata con el cuidado correspondiente:

  - Solo expone recycling_points (puntos físicos reales -- contenedores /
    centros de acopio, ya públicos por naturaleza).
  - PROHIBIDO exponer acá cualquier campo de candidate_zones: ml_score,
    priority_label, is_recommended, recommendation_reason, model_version,
    o cualquier otro dato de inferencia/zonas candidatas. Eso es contenido
    interno protegido por rol (GET /ml/recommendations).
  - NO sigue el patrón de "cuenta ciudadano con password" -- ese patrón se
    rechaza explícitamente tras el hardening de seguridad (ver
    auditoria-seguridad-backend.md I5). Sigue el patrón ya existente y
    correcto de GET /map/points: público de verdad, sin JWT de ningún tipo.

Prefijo /api/v1/public separado a propósito del resto de la API, para que
sea obvio en el código y en la tabla de rutas cuál es la superficie sin auth.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import limiter
from app.db.session import get_session
from app.services.geo_service import (
    PUBLIC_MATERIAL_ALIASES,
    get_public_nearby_recycling_points,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Rango real de Lima Metropolitana + margen razonable -- verificado contra
# los datos reales en Neon (candidate_zones: lat -12.17..-11.76,
# lon -77.18..-76.81; recycling_points: lat -12.15..-12.02, lon -77.12..-77.00).
# No se usó el rango sugerido en la tarea tal cual porque su ge=-77 de
# longitud habría rechazado puntos reales existentes (lon hasta -77.18).
_LIMA_LAT_MIN, _LIMA_LAT_MAX = -12.6, -11.6
_LIMA_LNG_MIN, _LIMA_LNG_MAX = -77.3, -76.6

_MAX_RADIUS_M = 5000


@router.get("/recycling-points")
@limiter.limit("30/minute")
async def public_recycling_points(
    request: Request,
    lat: float = Query(..., ge=_LIMA_LAT_MIN, le=_LIMA_LAT_MAX),
    lng: float = Query(..., ge=_LIMA_LNG_MIN, le=_LIMA_LNG_MAX),
    radius_m: int = Query(default=2000, ge=1),
    material: str | None = None,
    db: AsyncSession = Depends(get_session),
):
    """
    Público, sin autenticación -- puntos de reciclaje reales cerca de
    (lat, lng), hasta 30 req/min por IP. radius_m se clampea a 5000m (nunca
    error por pasarse). material, si se pasa, debe ser uno de los valores
    reconocidos (422 si no). Respuesta vacía es válida -- la mayoría de
    distritos todavía no tienen puntos cargados, y eso no es un error.
    """
    radius_m = min(radius_m, _MAX_RADIUS_M)

    canonical_material: str | None = None
    if material is not None:
        canonical_material = PUBLIC_MATERIAL_ALIASES.get(material.strip().lower())
        if canonical_material is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INVALID_MATERIAL",
                    "message": "material debe ser uno de los valores reconocidos.",
                    "allowed": sorted(set(PUBLIC_MATERIAL_ALIASES.values())),
                },
            )

    try:
        return await get_public_nearby_recycling_points(
            db, lat=lat, lng=lng, radius_m=radius_m, material=canonical_material,
        )
    except HTTPException:
        raise
    except Exception:
        # Sin fuga de detalles internos al cliente (mismo criterio que
        # I7/I8 del hardening) -- el detalle completo va al log del server.
        logger.exception("Fallo interno en GET /public/recycling-points")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": "No se pudo procesar la solicitud."},
        )
