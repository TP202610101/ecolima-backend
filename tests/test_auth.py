"""
tests/test_auth.py
Pruebas de autenticación JWT — login, logout, acceso protegido.
Requiere la base de datos de desarrollo con el usuario admin seed.
"""
import pytest
from httpx import AsyncClient

from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_login_success(client: AsyncClient):
    """Login con credenciales válidas retorna access_token y datos del usuario."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@ecolima.pe", "password": "AdminSeguro2026!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["role"] == "admin"


async def test_login_wrong_password(client: AsyncClient):
    """Login con contraseña incorrecta retorna 401."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@ecolima.pe", "password": "wrong-password"},
    )
    assert resp.status_code == 401


async def test_login_nonexistent_user(client: AsyncClient):
    """Login con email inexistente retorna 401."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "noexiste@test.com", "password": "any"},
    )
    assert resp.status_code == 401


async def test_protected_endpoint_without_token(client: AsyncClient):
    """Endpoint protegido sin token retorna 401 o 403 (sin autenticación)."""
    resp = await client.get("/api/v1/map/points")
    assert resp.status_code in (401, 403)


async def test_protected_endpoint_with_token(client: AsyncClient, admin_token: str):
    """Endpoint protegido con token válido responde (200 o 200 vacío)."""
    resp = await client.get(
        "/api/v1/map/districts",
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200


async def test_logout_requires_auth(client: AsyncClient):
    """Logout sin token retorna 401 o 403 (sin autenticación)."""
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code in (401, 403)
