from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_current_user
from app.core.security import create_access_token, verify_password
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import LoginRequest, TokenResponse

router = APIRouter()
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


@router.post("/login", response_model=TokenResponse)
async def login(credentials: LoginRequest, db: AsyncSession = Depends(get_session)):
    user = await User.get_by_email(db, credentials.email)

    if user and user.locked_until and user.locked_until > datetime.utcnow():
        remaining = max(1, int((user.locked_until - datetime.utcnow()).total_seconds() // 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiados intentos de acceso. Intenta en {remaining} minutos.",
        )

    if not user or not verify_password(credentials.password, user.hashed_password):
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
