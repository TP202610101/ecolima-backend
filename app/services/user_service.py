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
