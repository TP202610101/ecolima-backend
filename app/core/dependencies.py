from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import decode_access_token
from app.db.session import get_session
from app.models.user import User

bearer_scheme = HTTPBearer()
_optional_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales incorrectas")

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales incorrectas")
        user = await User.get_by_id(db, int(user_id))
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales incorrectas")
        if payload.get("tv", 0) != (user.token_version or 0):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales incorrectas")
        return user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Servicio temporalmente no disponible")


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User | None:
    """Como get_current_user, pero devuelve None en vez de lanzar 401 si no
    hay token o es inválido. Para endpoints de acceso mixto (público +
    autenticado) como GET /map/points, donde el modo "cercanos" (?lat=&lon=)
    es público y el resto requiere sesión."""
    if credentials is None:
        return None
    try:
        return await get_current_user(credentials=credentials, db=db)
    except HTTPException:
        return None


def require_role(*roles: str):
    async def role_checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tiene permisos suficientes")
        return user
    return role_checker
