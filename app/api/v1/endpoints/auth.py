from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.limiter import limiter
from app.core.security import create_access_token, get_password_hash, validate_password_strength, verify_password
from app.core.config import Settings
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import ForgotPasswordRequest, LoginRequest, ResetPasswordRequest, TokenResponse
from app.services.password_reset_service import (
    EmailSender,
    create_reset_token,
    get_password_reset_sender,
    get_valid_reset_token,
)

router = APIRouter()
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

_settings = Settings()

# Mensaje neutro: idéntico exista o no el correo, esté o no activa la cuenta,
# haya o no superado el límite de solicitudes -- no debe poder usarse para
# enumerar qué correos están registrados (mismo principio que _DUMMY_HASH).
_FORGOT_PASSWORD_RESPONSE = {
    "message": "Si el correo está registrado y activo, se envió un enlace de recuperación."
}

_INVALID_RESET_TOKEN_DETAIL = {
    "code": "INVALID_RESET_TOKEN",
    "message": "El enlace de recuperación es inválido, venció o ya fue usado. Solicita uno nuevo.",
}

# Hash dummy -- no corresponde a ninguna contraseña real. Existe solo para que
# verify_password() tarde lo mismo cuando el email no existe que cuando existe
# con contraseña incorrecta: sin esto, un email inexistente responde más
# rápido (nunca se llama a verify_password) y eso permite enumerar cuentas
# válidas por timing aunque el mensaje de error sea idéntico en ambos casos.
_DUMMY_HASH = get_password_hash("no-such-user-timing-safe-dummy")


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(request: Request, credentials: LoginRequest, db: AsyncSession = Depends(get_session)):
    user = await User.get_by_email(db, credentials.email)

    if user and user.locked_until and user.locked_until > datetime.utcnow():
        remaining = max(1, int((user.locked_until - datetime.utcnow()).total_seconds() // 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiados intentos de acceso. Intenta en {remaining} minutos.",
        )

    password_ok = verify_password(
        credentials.password,
        user.hashed_password if user else _DUMMY_HASH,
    )

    if not user or not password_ok:
        if user:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= MAX_LOGIN_ATTEMPTS:
                user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
            await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales incorrectas")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El usuario está inactivo")

    user.failed_login_count = 0
    user.locked_until = None
    await db.commit()

    access_token = create_access_token({
        "sub": str(user.user_id),
        "email": user.email,
        "tv": user.token_version or 0,
    })
    return {"access_token": access_token, "user": user}


@router.post("/logout")
async def logout(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    user.token_version = (user.token_version or 0) + 1
    await db.commit()
    return {"message": "Sesión cerrada"}


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_session),
    send_email: EmailSender = Depends(get_password_reset_sender),
):
    """Siempre responde 202 con el mismo mensaje, exista o no el correo, esté
    o no activa la cuenta, o se haya superado el límite de solicitudes
    recientes -- ninguna de esas condiciones debe ser observable desde
    afuera. Solo si el usuario existe, está activo y no superó el límite se
    crea un token y se intenta enviar el correo."""
    user = await User.get_by_email(db, payload.email)

    if user and user.is_active:
        raw_token = await create_reset_token(db, user)
        if raw_token:
            reset_url = f"{_settings.frontend_base_url}/restablecer?token={raw_token}"
            await send_email(user.email, reset_url)

    return _FORGOT_PASSWORD_RESPONSE


@router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_session),
):
    """400 si el token es inválido, venció o ya fue usado. 422 si la nueva
    contraseña no cumple la política -- en ese caso el token sigue vigente
    (no se quema hasta que el cambio de contraseña se aplica con éxito, para
    no obligar a pedir un enlace nuevo por un typo en la contraseña)."""
    token_row = await get_valid_reset_token(db, payload.token)
    if token_row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_RESET_TOKEN_DETAIL)

    try:
        validate_password_strength(payload.new_password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "WEAK_PASSWORD", "message": str(exc)},
        )

    user = await User.get_by_id(db, token_row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_RESET_TOKEN_DETAIL)

    user.hashed_password = get_password_hash(payload.new_password)
    # Invalida cualquier JWT emitido antes del cambio -- mismo mecanismo que
    # /logout y que la desactivación de usuario (ver get_current_user).
    user.token_version = (user.token_version or 0) + 1
    user.failed_login_count = 0
    user.locked_until = None
    token_row.used_at = datetime.utcnow()
    await db.commit()

    return {"message": "Contraseña actualizada correctamente."}
