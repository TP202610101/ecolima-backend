from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_current_user
from app.core.limiter import limiter
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import LoginRequest, TokenResponse

router = APIRouter()
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

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
