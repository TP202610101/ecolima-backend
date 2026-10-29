"""
Resultados V1 importados del paquete versionado: /api/v1/analisis/* (solo lectura).

El backend sirve lo que trae el paquete, sin recalcular la metodología ni
usar el modelo demo de /ml/*. Reglas de lectura que las respuestas respetan:
  - `apto_tamizaje` no es «viable»: la verificación en campo está pendiente;
  - la propensión (regresión logística) se da como percentil y solo es contexto;
  - la MCDA ordena con puntaje y rango; no hay categorías Alta/Media/Baja.

Todas las rutas aceptan `?version=` (paquete_version); sin él, la versión
activa. Cada respuesta lleva atribución y licencia de los datos (ODbL 1.0),
también en la cabecera X-Licencia-Datos.

Los endpoints demo (/ml/*, /map/*, /geo/*) no cambian.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_role
from app.db.session import get_session
from app.models.analisis import CLASES_OPORTUNIDAD, ESQUEMAS_MCDA, ESTADOS_TAMIZAJE, ROLES, VersionAnalisis
from app.models.user import User
from app.schemas.analisis import ListaVersiones, PaginaSitios
from app.services import analisis_consultas as consultas

router = APIRouter()

_GEO = "application/geo+json"
_MAX_LIMIT = 500

EstadoTamizaje = Literal[ESTADOS_TAMIZAJE]
Rol = Literal[ROLES]
ClaseOportunidad = Literal[CLASES_OPORTUNIDAD]
Esquema = Literal[ESQUEMAS_MCDA]


def _cabeceras(v: VersionAnalisis) -> dict[str, str]:
    return {"X-Licencia-Datos": v.licencia_datos, "X-Paquete-Version": v.paquete_version}


async def _version(
    version: str | None = Query(None, max_length=20, description="paquete_version; por defecto, la activa"),
    db: AsyncSession = Depends(get_session),
) -> VersionAnalisis:
    try:
        return await consultas.resolver_version(db, version)
    except consultas.VersionNoEncontrada as ex:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ex))


def _filtros(
    estado_tamizaje: EstadoTamizaje | None = None,
    rol: Rol | None = None,
    clase_oportunidad: ClaseOportunidad | None = None,
    ubigeo: str | None = Query(None, pattern=r"^1501\d{2}$"),
    en_top_k: bool | None = Query(None, description="Solo sitios dentro (true) o fuera (false) del top-k MCDA"),
    bbox: str | None = Query(None, description="min_lon,min_lat,max_lon,max_lat (WGS84)"),
) -> consultas.FiltrosSitios:
    caja = None
    if bbox is not None:
        try:
            caja = tuple(float(x) for x in bbox.split(","))
        except ValueError:
            caja = ()
        if len(caja) != 4 or caja[0] > caja[2] or caja[1] > caja[3]:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="bbox debe ser min_lon,min_lat,max_lon,max_lat")
    return consultas.FiltrosSitios(estado_tamizaje, rol, clase_oportunidad, ubigeo, en_top_k, caja)


# ── Versiones ──────────────────────────────────────────────────────────────────

@router.get("/versiones", response_model=ListaVersiones)
async def analisis_versiones(
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — paquetes importados y cuál está activo."""
    versiones = await consultas.listar_versiones(db)
    activa = next((v["paquete_version"] for v in versiones if v["activa"]), None)
    return {"activa": activa, "versiones": versiones}


@router.get("/versiones/activa")
async def analisis_version_activa(
    response: Response,
    _: User = Depends(require_role("admin", "analista")),
    v: VersionAnalisis = Depends(_version),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — versión activa (o ?version=): manifiesto resumido, conteos y avisos de lectura."""
    response.headers.update(_cabeceras(v))
    return {**await consultas.detalle_version(db, v), "atribucion": consultas.atribucion(v)}


# ── Sitios ─────────────────────────────────────────────────────────────────────

@router.get("/sitios", response_model=PaginaSitios)
async def analisis_sitios(
    response: Response,
    esquema: Esquema = consultas.ESQUEMA_PRINCIPAL,
    orden: Literal["rango", "sitio_id"] = "rango",
    limit: int = Query(100, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    filtros: consultas.FiltrosSitios = Depends(_filtros),
    _: User = Depends(require_role("admin", "analista")),
    v: VersionAnalisis = Depends(_version),
    db: AsyncSession = Depends(get_session),
):
    """
    admin, analista — sitios candidatos y puntos existentes, paginados.
    Filtros: estado_tamizaje, rol, clase_oportunidad, ubigeo, en_top_k, bbox.
    `esquema` elige el puntaje/rango MCDA (iguales por defecto). Orden por rango
    MCDA (los sitios sin MCDA al final) o por sitio_id.
    """
    total, items = await consultas.listar_sitios(db, v.version_id, filtros, esquema, orden, limit, offset)
    response.headers.update(_cabeceras(v))
    return {"paquete_version": v.paquete_version, "esquema": esquema, "total": total, "limit": limit,
            "offset": offset, "items": items, "avisos": consultas.AVISOS, "atribucion": consultas.atribucion(v)}


@router.get("/sitios/geojson")
async def analisis_sitios_geojson(
    esquema: Esquema = consultas.ESQUEMA_PRINCIPAL,
    filtros: consultas.FiltrosSitios = Depends(_filtros),
    _: User = Depends(require_role("admin", "analista")),
    v: VersionAnalisis = Depends(_version),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — los mismos sitios como FeatureCollection de puntos (sin paginar), para el mapa."""
    data = await consultas.sitios_geojson(db, v.version_id, filtros, esquema)
    data |= {"paquete_version": v.paquete_version, "esquema": esquema, "avisos": consultas.AVISOS,
             "atribucion": consultas.atribucion(v)}
    return JSONResponse(content=data, media_type=_GEO, headers=_cabeceras(v))


@router.get("/sitios/{sitio_id}")
async def analisis_sitio(
    sitio_id: str,
    response: Response,
    _: User = Depends(require_role("admin", "analista")),
    v: VersionAnalisis = Depends(_version),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — detalle de un sitio: tamizaje, variables, propensión, criterios K1–K4, MCDA y Monte Carlo."""
    if len(sitio_id) > 40:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sitio no encontrado")
    data = await consultas.detalle_sitio(db, v, sitio_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sitio no encontrado")
    response.headers.update(_cabeceras(v))
    return {**data, "paquete_version": v.paquete_version, "atribucion": consultas.atribucion(v)}


# ── Tamizaje, MCDA y distritos ─────────────────────────────────────────────────

@router.get("/tamizaje")
async def analisis_tamizaje(
    response: Response,
    _: User = Depends(require_role("admin", "analista")),
    v: VersionAnalisis = Depends(_version),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — conteos por estado de tamizaje, por regla T01–T05 y exclusiones."""
    response.headers.update(_cabeceras(v))
    return {**await consultas.resumen_tamizaje(db, v.version_id), "paquete_version": v.paquete_version,
            "atribucion": consultas.atribucion(v)}


@router.get("/mcda")
async def analisis_mcda(
    response: Response,
    _: User = Depends(require_role("admin", "analista")),
    v: VersionAnalisis = Depends(_version),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — método MCDA: criterios K1–K4, pesos por esquema, top-k, Monte Carlo y concordancia."""
    response.headers.update(_cabeceras(v))
    return {**await consultas.resumen_mcda(db, v), "paquete_version": v.paquete_version,
            "atribucion": consultas.atribucion(v)}


@router.get("/distritos")
async def analisis_distritos(
    esquema: Esquema = consultas.ESQUEMA_PRINCIPAL,
    geometria: bool = Query(True, description="false: solo propiedades y conteos"),
    simplificar: float | None = Query(None, gt=0, le=0.01, description="tolerancia en grados (p. ej. 0.0005)"),
    _: User = Depends(require_role("admin", "analista")),
    v: VersionAnalisis = Depends(_version),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — 43 límites distritales del paquete (MultiPolygon) con conteos de sitios y top-k."""
    data = await consultas.distritos_geojson(db, v.version_id, esquema, geometria, simplificar)
    data |= {"paquete_version": v.paquete_version, "atribucion": consultas.atribucion(v)}
    return JSONResponse(content=data, media_type=_GEO, headers=_cabeceras(v))


# ── Fuentes y modelo ───────────────────────────────────────────────────────────

@router.get("/fuentes")
async def analisis_fuentes(
    response: Response,
    _: User = Depends(require_role("admin", "analista")),
    v: VersionAnalisis = Depends(_version),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — fuentes con licencia y texto de atribución exigido por cada proveedor."""
    response.headers.update(_cabeceras(v))
    return {"paquete_version": v.paquete_version, "fuentes": await consultas.listar_fuentes(db, v.version_id),
            "atribucion": consultas.atribucion(v)}


@router.get("/modelo")
async def analisis_modelo(
    response: Response,
    _: User = Depends(require_role("admin", "analista")),
    v: VersionAnalisis = Depends(_version),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — ficha del modelo de propensión (regresión logística), métricas por semilla y parámetros."""
    data = await consultas.modelo(db, v.version_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La versión no tiene modelo")
    response.headers.update(_cabeceras(v))
    return {**data, "paquete_version": v.paquete_version, "atribucion": consultas.atribucion(v)}
