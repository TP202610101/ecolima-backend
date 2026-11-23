from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_role
from app.core.limiter import limiter
from app.core.security import validate_password_strength
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, UserRoleUpdate, UserStatusUpdate
from app.schemas.user_bulk import BulkConfirmResponse, BulkValidationResponse
from app.services import user_service
from app.services.password_reset_service import EmailSender, get_invitation_sender
from app.services.user_bulk_service import create_users_in_bulk, validate_bulk_upload

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


@router.post("/bulk/validate", response_model=BulkValidationResponse)
@limiter.limit("10/minute")
async def validate_bulk_users(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """No crea nada -- valida el CSV (correo,nombre,rol) y devuelve el
    detalle fila por fila. 200 siempre que el archivo sea legible, aunque
    tenga filas con error (ahí "valid" sale en false)."""
    raw_bytes = await file.read()
    return await validate_bulk_upload(db, raw_bytes)


@router.post(
    "/bulk/confirm",
    response_model=BulkConfirmResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/minute")
async def confirm_bulk_users(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
    send_invitation: EmailSender = Depends(get_invitation_sender),
):
    """Revalida el MISMO archivo. Si una sola fila tiene error, 409 sin crear
    nada. Si todo es válido, crea todas las cuentas (rol analista, activas,
    contraseña aleatoria inutilizable) en una sola transacción y envía la
    invitación a cada una -- un correo que falla no deshace la cuenta, solo
    aparece en invitations_failed."""
    raw_bytes = await file.read()
    return await create_users_in_bulk(db, current_user.user_id, raw_bytes, send_invitation)


@router.patch("/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    if payload.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_ROLE_ERROR_DETAIL)

    user = await user_service.update_user_role(db, user_id, payload.role, current_user.user_id)
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
    user = await user_service.set_user_active(db, user_id, payload.is_active, current_user.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return user
