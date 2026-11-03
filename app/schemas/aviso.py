from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.core.config import AVISO_CLAVES_PERMITIDAS

# Enum construido desde la configuración (no hardcodeado acá ni en el
# endpoint) -- una clave fuera de AVISO_CLAVES_PERMITIDAS da 422 tanto en el
# body (POST /me/avisos/confirmar) como en query params (GET /me/avisos,
# GET /admin/avisos/confirmaciones), porque FastAPI valida el tipo del campo
# en ambos casos.
AvisoClave = Enum("AvisoClave", {clave: clave for clave in AVISO_CLAVES_PERMITIDAS}, type=str)


class AvisoConfirmarRequest(BaseModel):
    clave: AvisoClave
    version: str = Field(..., min_length=1, max_length=20)


class AvisoEstadoResponse(BaseModel):
    clave: str
    version: str
    confirmado: bool
    confirmado_en: datetime | None = None


class AvisoConfirmacionUsuario(BaseModel):
    """Una fila del reporte admin: estado de una cuenta activa (admin o
    analista) para una (clave, version) puntual."""
    user_id: int
    email: str
    full_name: str | None = None
    role: str
    confirmado: bool
    confirmado_en: datetime | None = None


class AvisoConfirmacionesReporte(BaseModel):
    clave: str
    version: str
    usuarios: list[AvisoConfirmacionUsuario]
