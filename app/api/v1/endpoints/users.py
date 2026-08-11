from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_role
from app.core.security import validate_password_strength
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, UserRoleUpdate, UserStatusUpdate
from app.services import user_service

router = APIRouter()
# "ciudadano" es un rol conceptual (acceso público, sin autenticación) -- no
# debe poder existir como cuenta con contraseña, o tendría más acceso del que
# el diseño le asigna (cualquier endpoint con Depends(get_current_user) sin
# restricción de rol adicional). Ver auditoria-seguridad-backend.md I5.
ALLOWED_ROLES = {"admin", "analista"}
_ROLE_ERROR_DETAIL = {
    "code": "INVALID_ROLE",
    "message": "Rol inválido. Roles permitidos: admin, analista.",
    "allowed": sorted(ALLOWED_ROLES),
}


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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_ROLE_ERROR_DETAIL)

    try:
        validate_password_strength(user_in.password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "WEAK_PASSWORD", "message": str(exc)},
        )

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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_ROLE_ERROR_DETAIL)

    user = await user_service.update_user_role(db, user_id, payload.role)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return user


@router.patch("/{user_id}/status", response_model=UserResponse)
async def update_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """admin — activa/desactiva un usuario. Al desactivar, invalida de
    inmediato cualquier token ya emitido (bump de token_version) -- antes no
    existía forma de revocar acceso de una cuenta comprometida salvo un
    script directo contra la BD. Ver auditoria-seguridad-backend.md I4."""
    user = await user_service.set_user_active(db, user_id, payload.is_active)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return user
