from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.user import User


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


async def update_user_role(db: AsyncSession, user_id: int, new_role: str) -> User | None:
    user = await User.get_by_id(db, user_id)
    if not user:
        return None
    user.role = new_role
    await db.commit()
    await db.refresh(user)
    return user


async def set_user_active(db: AsyncSession, user_id: int, is_active: bool) -> User | None:
    """Activa/desactiva un usuario. Al desactivar, bump de token_version para
    invalidar cualquier token ya emitido -- sin esto, una sesión activa
    seguiría funcionando hasta que el token expirara por su cuenta (ver
    auditoria-seguridad-backend.md I4)."""
    user = await User.get_by_id(db, user_id)
    if not user:
        return None
    user.is_active = is_active
    if not is_active:
        user.token_version = (user.token_version or 0) + 1
    await db.commit()
    await db.refresh(user)
    return user
