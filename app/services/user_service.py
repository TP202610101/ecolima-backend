from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.user import User

_LAST_ADMIN_ERROR_DETAIL = {
    "code": "LAST_ADMIN",
    "message": "No se puede dejar el sistema sin administradores activos.",
}
_ADMIN_IMMUTABLE_ERROR_DETAIL = {
    "code": "ADMIN_IMMUTABLE",
    "message": "No puedes modificar a otro administrador.",
}
_INACTIVE_CANNOT_PROMOTE_ERROR_DETAIL = {
    "code": "INACTIVE_CANNOT_PROMOTE",
    "message": "Activa la cuenta antes de promoverla a administrador.",
}


def _reject_if_other_admin(user: User, acting_user_id: int) -> None:
    """Un admin no puede modificar rol/estado de OTRO admin -- solo analistas,
    o a sí mismo (bloqueado además por isSelf en el frontend). Evita que un
    admin pueda degradar/desactivar a otro admin desde la app; revocar un
    admin real requiere acción manual en la BD (decisión de diseño, ver
    PLAN_BACKEND.md). Se evalúa ANTES que LAST_ADMIN: si el objetivo es otro
    admin, esto ya rechaza y LAST_ADMIN ni se llega a evaluar para ese caso."""
    if user.role == "admin" and user.user_id != acting_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_ADMIN_IMMUTABLE_ERROR_DETAIL)


async def _is_last_active_admin(db: AsyncSession, user: User) -> bool:
    """True si `user` es admin activo y es el ÚNICO admin activo -- es decir,
    desactivarlo o quitarle el rol dejaría al sistema con cero admins.
    Excluye al propio user_id del conteo (no se cuenta a sí mismo dos veces)."""
    if user.role != "admin" or not user.is_active:
        return False
    result = await db.execute(
        select(func.count()).select_from(User).where(
            User.role == "admin",
            User.is_active.is_(True),
            User.user_id != user.user_id,
        )
    )
    other_active_admins = result.scalar_one()
    return other_active_admins == 0


async def list_users(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User))
    return list(result.scalars().all())


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    role: str,
) -> User:
    return await User.create(
        db,
        email=email,
        hashed_password=get_password_hash(password),
        full_name=full_name,
        role=role,
        is_active=True,
    )


async def update_user_role(
    db: AsyncSession, user_id: int, new_role: str, acting_user_id: int
) -> User | None:
    user = await User.get_by_id(db, user_id)
    if not user:
        return None
    # Orden: ADMIN_IMMUTABLE -> INACTIVE_CANNOT_PROMOTE -> LAST_ADMIN. No se
    # solapan en la práctica: ADMIN_IMMUTABLE exige que el objetivo YA sea
    # admin; INACTIVE_CANNOT_PROMOTE exige new_role=='admin' (ascenso, el
    # objetivo normalmente es un analista, no un admin); LAST_ADMIN exige
    # new_role!='admin' (descenso). Evita el admin-fantasma: promover a admin
    # una cuenta inactiva la dejaría atrapada (admin + inactiva no se puede
    # reactivar, porque ADMIN_IMMUTABLE bloquearía a cualquier otro admin).
    _reject_if_other_admin(user, acting_user_id)
    if new_role == "admin" and not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_INACTIVE_CANNOT_PROMOTE_ERROR_DETAIL
        )
    if new_role != "admin" and await _is_last_active_admin(db, user):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_LAST_ADMIN_ERROR_DETAIL)
    user.role = new_role
    await db.commit()
    await db.refresh(user)
    return user


async def set_user_active(
    db: AsyncSession, user_id: int, is_active: bool, acting_user_id: int
) -> User | None:
    """Activa/desactiva un usuario. Al desactivar, bump de token_version para
    invalidar cualquier token ya emitido -- sin esto, una sesión activa
    seguiría funcionando hasta que el token expirara por su cuenta (ver
    auditoria-seguridad-backend.md I4)."""
    user = await User.get_by_id(db, user_id)
    if not user:
        return None
    _reject_if_other_admin(user, acting_user_id)
    if not is_active and await _is_last_active_admin(db, user):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_LAST_ADMIN_ERROR_DETAIL)
    user.is_active = is_active
    if not is_active:
        user.token_version = (user.token_version or 0) + 1
    await db.commit()
    await db.refresh(user)
    return user
