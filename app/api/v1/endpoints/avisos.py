from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.aviso import (
    AvisoClave,
    AvisoConfirmacionesReporte,
    AvisoConfirmarRequest,
    AvisoEstadoResponse,
)
from app.services import aviso_service

# Montado en /api/v1/me -- cualquier usuario autenticado (admin o analista)
# consulta y confirma SU PROPIO estado, para cualquiera de las claves de
# AVISO_CLAVES_PERMITIDAS (hoy, los tres modales de ayuda del Análisis: "cómo
# se ordenan", "fuentes y limitaciones", "acerca del modelo"). No hay user_id
# en el payload: el usuario sale siempre de get_current_user, nunca se puede
# confirmar "por otro". El backend nunca bloquea nada -- solo guarda y
# consulta confirmaciones; es el frontend quien decide cuándo pedirlas.
router = APIRouter()

# Montado en /api/v1/admin/avisos -- aparte, porque es un recurso distinto
# (reporte sobre todas las cuentas) y solo accesible por admin.
admin_router = APIRouter()


@router.get("/avisos", response_model=AvisoEstadoResponse)
async def estado_aviso(
    clave: AvisoClave = Query(..., description="Una de AVISO_CLAVES_PERMITIDAS"),
    version: str = Query(..., min_length=1, max_length=20, description="Versión del aviso a verificar"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Si el usuario ya confirmó esta (clave, version) exacta. Si la versión
    cambió respecto a la que confirmó antes, acá sale confirmado=False --
    la confirmación es por versión, no se hereda de una anterior. Cada clave
    es independiente: confirmar una no cuenta para las demás."""
    confirmacion = await aviso_service.get_confirmacion(db, current_user.user_id, clave.value, version)
    return AvisoEstadoResponse(
        clave=clave.value,
        version=version,
        confirmado=confirmacion is not None,
        confirmado_en=confirmacion.confirmado_en if confirmacion else None,
    )


@router.post("/avisos/confirmar", response_model=AvisoEstadoResponse, status_code=status.HTTP_200_OK)
async def confirmar_aviso(
    payload: AvisoConfirmarRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Idempotente: repetir la misma (clave, version) no crea una fila nueva
    ni cambia confirmado_en -- devuelve la confirmación ya existente."""
    confirmacion = await aviso_service.confirmar_lectura(
        db, current_user.user_id, payload.clave.value, payload.version
    )
    return AvisoEstadoResponse(
        clave=confirmacion.clave,
        version=confirmacion.version,
        confirmado=True,
        confirmado_en=confirmacion.confirmado_en,
    )


@admin_router.get("/confirmaciones", response_model=AvisoConfirmacionesReporte)
async def reporte_confirmaciones(
    clave: AvisoClave = Query(..., description="Una de AVISO_CLAVES_PERMITIDAS"),
    version: str = Query(..., min_length=1, max_length=20),
    _: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """admin -- una fila por cada cuenta ACTIVA de rol admin o analista
    (confirmó o no, y cuándo). Ciudadanos (sin cuenta) y cuentas desactivadas
    no aparecen -- igual que list_users."""
    usuarios = await aviso_service.listar_confirmaciones_usuarios_activos(db, clave.value, version)
    return AvisoConfirmacionesReporte(clave=clave.value, version=version, usuarios=usuarios)
