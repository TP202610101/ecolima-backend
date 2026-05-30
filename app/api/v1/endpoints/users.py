from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_current_user, require_role
from app.core.security import get_password_hash
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, UserRoleUpdate

router = APIRouter()
ALLOWED_ROLES = {"admin", "analista", "ciudadano"}


@router.get("/", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(User))
    return result.scalars().all()


@router.post("/", response_model=UserResponse)
async def create_user(
    user_in: UserCreate,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    if user_in.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rol inválido")

    existing_user = await User.get_by_email(db, user_in.email)
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El email ya existe")

    user = await User.create(
        db,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        role=user_in.role,
        is_active=True,
    )
    return user


@router.patch("/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    if payload.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rol inválido")

    user = await User.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    user.role = payload.role
    await db.commit()
    await db.refresh(user)
    return user
