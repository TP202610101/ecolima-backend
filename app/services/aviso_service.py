from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aviso_confirmacion import AvisoConfirmacion
from app.models.user import User

# Mismos roles que ALLOWED_ROLES en app/api/v1/endpoints/users.py -- duplicado
# por valor (no importado desde ahí) para no crear una dependencia
# services -> endpoints. "ciudadano" es acceso público sin cuenta: no debería
# poder existir como fila en `users`, pero igual se filtra explícitamente acá
# por si alguna vez aparece una.
_ROLES_CON_CUENTA = ("admin", "analista")


async def get_confirmacion(
    db: AsyncSession, user_id: int, clave: str, version: str
) -> AvisoConfirmacion | None:
    return (await db.execute(
        select(AvisoConfirmacion).where(
            AvisoConfirmacion.user_id == user_id,
            AvisoConfirmacion.clave == clave,
            AvisoConfirmacion.version == version,
        )
    )).scalar_one_or_none()


async def confirmar_lectura(
    db: AsyncSession, user_id: int, clave: str, version: str
) -> AvisoConfirmacion:
    """Idempotente: si ya existe una confirmación para este (user_id, clave,
    version) la devuelve sin tocarla (no actualiza confirmado_en -- la
    primera confirmación es la que cuenta). Si no existe, la crea."""
    existing = await get_confirmacion(db, user_id, clave, version)
    if existing is not None:
        return existing

    confirmacion = AvisoConfirmacion(
        user_id=user_id,
        clave=clave,
        version=version,
        confirmado_en=datetime.utcnow(),
    )
    db.add(confirmacion)
    await db.commit()
    await db.refresh(confirmacion)
    return confirmacion


async def listar_confirmaciones_usuarios_activos(
    db: AsyncSession, clave: str, version: str
) -> list[dict]:
    """Un registro por cada cuenta ACTIVA de rol admin o analista (no por
    cada confirmación) -- incluye a quienes todavía no confirmaron, con
    confirmado=False. Ciudadanos (sin cuenta) e inactivos no aparecen."""
    rows = (await db.execute(
        select(User.user_id, User.email, User.full_name, User.role, AvisoConfirmacion.confirmado_en)
        .outerjoin(
            AvisoConfirmacion,
            (AvisoConfirmacion.user_id == User.user_id)
            & (AvisoConfirmacion.clave == clave)
            & (AvisoConfirmacion.version == version),
        )
        .where(User.is_active.is_(True), User.role.in_(_ROLES_CON_CUENTA))
        .order_by(User.email)
    )).all()

    return [
        {
            "user_id": row.user_id,
            "email": row.email,
            "full_name": row.full_name,
            "role": row.role,
            "confirmado": row.confirmado_en is not None,
            "confirmado_en": row.confirmado_en,
        }
        for row in rows
    ]
