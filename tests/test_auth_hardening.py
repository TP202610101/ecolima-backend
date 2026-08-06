"""
tests/test_auth_hardening.py

Tests de los hallazgos de auditoria-seguridad-backend.md que sí se
corrigieron con cambios de código acotados:
  - C2: rate limit dedicado en /login (5/minute)
  - I1: mitigación de enumeración de cuentas por timing
  - I2: JWT inválido/expirado -> 401, no 503
  - I4: desactivar un usuario invalida tokens ya emitidos
  - I5: no se puede crear un usuario con rol "ciudadano"

No tocan Neon de forma permanente: los usuarios de prueba que crean se
borran en el teardown.
"""
import time
from datetime import timedelta

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, verify_password
from tests.conftest import auth_headers


# ── C2: rate limit de /login ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_rate_limited_after_5_attempts_per_minute(client: AsyncClient):
    """El 6to intento de login en menos de un minuto (misma IP de test)
    responde 429, sin importar si las credenciales son válidas o no."""
    responses = []
    for _ in range(6):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "no-existe-rate-limit-test@example.com", "password": "cualquiera"},
        )
        responses.append(resp.status_code)

    assert responses[:5] == [401, 401, 401, 401, 401]
    assert responses[5] == 429


# ── I1: mitigación de enumeración por timing ────────────────────────────────

def test_dummy_hash_is_a_real_bcrypt_hash_not_a_password():
    """_DUMMY_HASH debe ser un hash bcrypt válido (no una contraseña en texto
    plano) -- así verify_password() hace el mismo trabajo criptográfico que
    contra un hash real, igualando el tiempo de respuesta."""
    from app.api.v1.endpoints.auth import _DUMMY_HASH

    assert _DUMMY_HASH.startswith("$2b$")
    assert verify_password("admin", _DUMMY_HASH) is False
    assert verify_password("cualquiera", _DUMMY_HASH) is False


@pytest.mark.asyncio
async def test_missing_user_and_wrong_password_take_similar_time(client: AsyncClient):
    """Antes del fix, un email inexistente respondía casi instantáneo (nunca
    se llamaba a verify_password) mientras que una password incorrecta contra
    un usuario real tardaba lo que tarda bcrypt (~decenas/cientos de ms) --
    eso es un canal de timing para enumerar cuentas. Tolerancia amplia a
    propósito (evitar flakiness por jitter de CI): solo se busca que no haya
    una diferencia de orden de magnitud como la que había antes del fix.
    """
    from app.core.config import Settings

    settings = Settings()

    t0 = time.perf_counter()
    await client.post(
        "/api/v1/auth/login",
        json={"email": "no-existe-timing-test@example.com", "password": "cualquiera"},
    )
    t_missing = time.perf_counter() - t0

    t0 = time.perf_counter()
    await client.post(
        "/api/v1/auth/login",
        json={"email": settings.admin_email, "password": "password-incorrecta-a-proposito"},
    )
    t_wrong = time.perf_counter() - t0

    slower, faster = max(t_missing, t_wrong), min(t_missing, t_wrong)
    assert slower < faster * 5 + 0.2, (
        f"Diferencia de timing sospechosa: usuario inexistente={t_missing:.3f}s, "
        f"password incorrecta={t_wrong:.3f}s -- posible canal de enumeración."
    )


# ── I2: JWT inválido/expirado -> 401, nunca 503 ────────────────────────────

@pytest.mark.asyncio
async def test_expired_token_returns_401_not_503(client: AsyncClient):
    token = create_access_token({"sub": "1", "tv": 0}, expires_delta=timedelta(seconds=-10))
    resp = await client.get("/api/v1/map/districts", headers=auth_headers(token))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_malformed_token_returns_401_not_503(client: AsyncClient):
    resp = await client.get("/api/v1/map/districts", headers=auth_headers("no-soy-un-jwt-valido"))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_signed_with_wrong_key_returns_401_not_503(client: AsyncClient):
    bad_token = jwt.encode(
        {"sub": "1", "tv": 0},
        "una-clave-completamente-distinta-de-32-caracteres-o-mas",
        algorithm="HS256",
    )
    resp = await client.get("/api/v1/map/districts", headers=auth_headers(bad_token))
    assert resp.status_code == 401


# ── I5: rol "ciudadano" no puede existir como cuenta ───────────────────────

@pytest.mark.asyncio
async def test_create_user_rejects_ciudadano_role(client: AsyncClient, admin_token: str):
    resp = await client.post(
        "/api/v1/admin/users",
        json={"email": "ciudadano-test@example.com", "password": "PasswordValida123!", "role": "ciudadano"},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_ROLE"


@pytest.mark.asyncio
async def test_update_user_role_rejects_ciudadano_role(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """Tampoco se puede degradar/promover a un usuario existente a rol ciudadano."""
    email = "pytest_role_update_target@example.com"
    try:
        create_resp = await client.post(
            "/api/v1/admin/users",
            json={"email": email, "password": "PasswordValida123!", "role": "analista"},
            headers=auth_headers(admin_token),
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["user_id"]

        resp = await client.patch(
            f"/api/v1/admin/users/{user_id}/role",
            json={"role": "ciudadano"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "INVALID_ROLE"
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


# ── I4: desactivar un usuario invalida tokens ya emitidos ──────────────────

@pytest.mark.asyncio
async def test_deactivated_user_cannot_use_previous_token_reactivated_can_login_again(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    email = "pytest_deactivation_target@example.com"
    password = "PasswordValida123!"
    try:
        create_resp = await client.post(
            "/api/v1/admin/users",
            json={"email": email, "password": password, "role": "analista"},
            headers=auth_headers(admin_token),
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["user_id"]

        login_resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login_resp.status_code == 200
        old_token = login_resp.json()["access_token"]

        # el token funciona antes de desactivar
        check_resp = await client.get("/api/v1/map/districts", headers=auth_headers(old_token))
        assert check_resp.status_code == 200

        deactivate_resp = await client.patch(
            f"/api/v1/admin/users/{user_id}/status",
            json={"is_active": False},
            headers=auth_headers(admin_token),
        )
        assert deactivate_resp.status_code == 200
        assert deactivate_resp.json()["is_active"] is False

        # el token viejo ya no sirve, aunque no haya expirado
        blocked_resp = await client.get("/api/v1/map/districts", headers=auth_headers(old_token))
        assert blocked_resp.status_code == 401

        # ni siquiera puede loguearse de nuevo mientras esté desactivado
        relogin_blocked = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert relogin_blocked.status_code == 403

        reactivate_resp = await client.patch(
            f"/api/v1/admin/users/{user_id}/status",
            json={"is_active": True},
            headers=auth_headers(admin_token),
        )
        assert reactivate_resp.status_code == 200
        assert reactivate_resp.json()["is_active"] is True

        # reactivado: login de nuevo (token nuevo) funciona
        relogin_resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert relogin_resp.status_code == 200
        new_token = relogin_resp.json()["access_token"]

        final_check = await client.get("/api/v1/map/districts", headers=auth_headers(new_token))
        assert final_check.status_code == 200
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()
