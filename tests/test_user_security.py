"""
tests/test_user_security.py
Dos mejoras de seguridad en admin/users:
  A. No se puede dejar el sistema sin ningún admin activo (desactivar o
     degradar al último admin se rechaza con 409 LAST_ADMIN).
  B. Política de contraseña reforzada (min 8, mayúscula, minúscula, número)
     en la creación de usuarios -- 422 WEAK_PASSWORD si no cumple.

No tocan Neon de forma permanente: los usuarios de prueba se borran en el
teardown; el/los admin(s) reales que se desactivan temporalmente para simular
el escenario de "último admin" se restauran antes de terminar el test.
"""
import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import user_service
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_VALID_PASSWORD = "PasswordValida123!"


async def _create_user(client: AsyncClient, admin_token: str, email: str, role: str) -> int:
    resp = await client.post(
        "/api/v1/admin/users",
        json={"email": email, "password": _VALID_PASSWORD, "role": role},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user_id"]


# ── Tarea A: no quedarse sin admins ─────────────────────────────────────────

async def test_deactivate_only_active_admin_is_rejected(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """Deja a un admin de prueba como el ÚNICO admin activo (desactivando
    temporalmente cualquier otro admin real por SQL directo -- nunca por la
    API, para no invalidar el token que este mismo test sigue usando), y
    confirma que desactivarlo se rechaza. Llama al service directamente
    (no HTTP) para el intento bloqueado, así el token de admin_token nunca
    se ve afectado por el estado que estamos manipulando."""
    email = "pytest_last_admin_deactivate@example.com"
    other_ids: list[int] = []
    try:
        target_id = await _create_user(client, admin_token, email, "admin")

        rows = (await db_session.execute(
            text("SELECT user_id FROM users WHERE role = 'admin' AND is_active = true AND user_id != :id"),
            {"id": target_id},
        )).fetchall()
        other_ids = [row[0] for row in rows]
        for uid in other_ids:
            await db_session.execute(text("UPDATE users SET is_active = false WHERE user_id = :id"), {"id": uid})
        await db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await user_service.set_user_active(db_session, target_id, False)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "LAST_ADMIN"
    finally:
        for uid in other_ids:
            await db_session.execute(text("UPDATE users SET is_active = true WHERE user_id = :id"), {"id": uid})
        if other_ids:
            await db_session.commit()
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


async def test_demote_only_active_admin_is_rejected(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """Mismo escenario que arriba pero degradando el rol (admin -> analista)
    en vez de desactivar."""
    email = "pytest_last_admin_demote@example.com"
    other_ids: list[int] = []
    try:
        target_id = await _create_user(client, admin_token, email, "admin")

        rows = (await db_session.execute(
            text("SELECT user_id FROM users WHERE role = 'admin' AND is_active = true AND user_id != :id"),
            {"id": target_id},
        )).fetchall()
        other_ids = [row[0] for row in rows]
        for uid in other_ids:
            await db_session.execute(text("UPDATE users SET is_active = false WHERE user_id = :id"), {"id": uid})
        await db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await user_service.update_user_role(db_session, target_id, "analista")
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "LAST_ADMIN"
    finally:
        for uid in other_ids:
            await db_session.execute(text("UPDATE users SET is_active = true WHERE user_id = :id"), {"id": uid})
        if other_ids:
            await db_session.commit()
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


async def test_deactivate_admin_allowed_when_other_admins_remain_active(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """Con el admin seed + este segundo admin ambos activos, desactivar el
    segundo debe funcionar normal (no es el último)."""
    email = "pytest_not_last_admin_deactivate@example.com"
    try:
        target_id = await _create_user(client, admin_token, email, "admin")

        resp = await client.patch(
            f"/api/v1/admin/users/{target_id}/status",
            json={"is_active": False},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


async def test_demote_admin_allowed_when_other_admins_remain_active(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    email = "pytest_not_last_admin_demote@example.com"
    try:
        target_id = await _create_user(client, admin_token, email, "admin")

        resp = await client.patch(
            f"/api/v1/admin/users/{target_id}/role",
            json={"role": "analista"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "analista"
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


# ── Tarea B: política de contraseña reforzada ───────────────────────────────

@pytest.mark.parametrize("bad_password", [
    "Short1a",        # 7 caracteres -- muy corta
    "nouppercase1",   # sin mayúscula
    "NOLOWERCASE1",   # sin minúscula
    "NoDigitsHere",   # sin número
])
async def test_create_user_rejects_weak_password(
    client: AsyncClient, admin_token: str, bad_password: str
):
    resp = await client.post(
        "/api/v1/admin/users",
        json={"email": "pytest_weak_password_test@example.com", "password": bad_password, "role": "analista"},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "WEAK_PASSWORD"


async def test_create_user_accepts_valid_password(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    email = "pytest_valid_password_test@example.com"
    try:
        resp = await client.post(
            "/api/v1/admin/users",
            json={"email": email, "password": _VALID_PASSWORD, "role": "analista"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 201
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()
