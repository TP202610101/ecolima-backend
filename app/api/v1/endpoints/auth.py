from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import bearer_scheme, get_current_user
from app.core.security import create_access_token, revoke_token, verify_password
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import LoginRequest, TokenResponse

router = APIRouter()
FAILED_LOGIN_ATTEMPTS: dict[str, int] = {}
MAX_LOGIN_ATTEMPTS = 5


def _increment_failed_attempts(email: str) -> None:
    FAILED_LOGIN_ATTEMPTS[email] = FAILED_LOGIN_ATTEMPTS.get(email, 0) + 1


def _reset_failed_attempts(email: str) -> None:
    FAILED_LOGIN_ATTEMPTS.pop(email, None)


def _is_account_locked(email: str) -> bool:
    return FAILED_LOGIN_ATTEMPTS.get(email, 0) >= MAX_LOGIN_ATTEMPTS


@router.post("/login", response_model=TokenResponse)
async def login(credentials: LoginRequest, db: AsyncSession = Depends(get_session)):
    if _is_account_locked(credentials.email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos de acceso. Intenta nuevamente más tarde.",
        )

    user = await User.get_by_email(db, credentials.email)
    if not user or not verify_password(credentials.password, user.hashed_password):
        _increment_failed_attempts(credentials.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales incorrectas")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El usuario está inactivo")

    _reset_failed_attempts(credentials.email)
    access_token = create_access_token({"sub": str(user.user_id), "email": user.email})
    return {"access_token": access_token, "user": user}


@router.post("/logout")
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    user: User = Depends(get_current_user),
):
    revoke_token(credentials.credentials)
    return {"message": "Sesión cerrada"}
