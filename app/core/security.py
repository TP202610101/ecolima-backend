import re
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.core.config import Settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = Settings()

ALGORITHM = "HS256"


def validate_password_strength(password: str) -> None:
    """Levanta ValueError si la contraseña no cumple la política mínima:
    >=8 caracteres, al menos una mayúscula, una minúscula y un número.
    Símbolos NO son obligatorios. Única fuente de verdad de la política --
    usada tanto por POST /admin/users como por los scripts de seed/reset
    (seed_admin.py, reset_admin.py, create_analista.py) para que un
    ADMIN_PASSWORD/ANALISTA_PASSWORD débil en el entorno también falle."""
    missing = []
    if len(password) < 8:
        missing.append("al menos 8 caracteres")
    if not re.search(r"[A-Z]", password):
        missing.append("al menos una mayúscula")
    if not re.search(r"[a-z]", password):
        missing.append("al menos una minúscula")
    if not re.search(r"[0-9]", password):
        missing.append("al menos un número")
    if missing:
        raise ValueError("La contraseña debe tener " + ", ".join(missing) + ".")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return payload
    except JWTError as exc:
        raise exc
