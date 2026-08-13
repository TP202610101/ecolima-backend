from pydantic import BaseModel, EmailStr
from datetime import datetime


class UserCreate(BaseModel):
    email: EmailStr
    # Política completa (min 8, mayúscula, minúscula, número) se valida en el
    # endpoint vía validate_password_strength -- no aquí, para que TODAS las
    # violaciones (incluida longitud) devuelvan el mismo código WEAK_PASSWORD
    # en vez de mezclar el formato de error genérico de Pydantic con el
    # formato {code, message} que usa el resto de la API.
    password: str
    full_name: str | None = None
    role: str = "analista"


class UserRoleUpdate(BaseModel):
    role: str


class UserStatusUpdate(BaseModel):
    is_active: bool


class UserResponse(BaseModel):
    user_id: int
    email: EmailStr
    full_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
