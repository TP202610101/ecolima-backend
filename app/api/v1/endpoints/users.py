from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, UserRoleUpdate
from app.services import user_service

router = APIRouter()
ALLOWED_ROLES = {"admin", "analista", "ciudadano"}


@router.get("", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    return await user_service.list_users(db)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_in: UserCreate,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    if user_in.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rol inválido")

    if await User.get_by_email(db, user_in.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El email ya existe")

    return await user_service.create_user(
        db,
        email=user_in.email,
        password=user_in.password,
        full_name=user_in.full_name,
        role=user_in.role,
    )


@router.patch("/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    if payload.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rol inválido")

    user = await user_service.update_user_role(db, user_id, payload.role)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return user
