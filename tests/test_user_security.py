"""
tests/test_user_security.py
Cuatro mejoras de seguridad en admin/users:
  A. No se puede dejar el sistema sin ningún admin activo (desactivar o
     degradar al último admin activo se rechaza con 409 LAST_ADMIN).
  B. Política de contraseña reforzada (min 8, mayúscula, minúscula, número)
     en la creación de usuarios -- 422 WEAK_PASSWORD si no cumple.
  C. Un admin no puede modificar rol/estado de OTRO admin (403
     ADMIN_IMMUTABLE) -- solo gestiona analistas. Revocar un admin real
     requiere acción manual en la BD (decisión de diseño).
  D. No se puede promover a admin una cuenta desactivada (409
     INACTIVE_CANNOT_PROMOTE) -- evita el admin-fantasma: admin + inactivo,
     atrapado porque ADMIN_IMMUTABLE bloquearía a cualquier otro admin que
     intente reactivarlo.

Orden de evaluación en update_user_role: ADMIN_IMMUTABLE ->
INACTIVE_CANNOT_PROMOTE -> LAST_ADMIN. No se solapan en la práctica:
ADMIN_IMMUTABLE exige que el objetivo YA sea admin; INACTIVE_CANNOT_PROMOTE
exige new_role=='admin' (ascenso); LAST_ADMIN exige new_role!='admin'
(descenso) -- universos disjuntos. En set_user_active solo aplican
ADMIN_IMMUTABLE y LAST_ADMIN (D es exclusivo del cambio de rol).

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


# ── Tarea A: no quedarse sin admins (probado como auto-modificación, el único
#    camino que llega a LAST_ADMIN ahora que ADMIN_IMMUTABLE existe -- ver
#    Tarea C) ──────────────────────────────────────────────────────────────

async def test_deactivate_only_active_admin_is_rejected(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """Deja a un admin de prueba como el ÚNICO admin activo (desactivando
    temporalmente cualquier otro admin real por SQL directo -- nunca por la
    API, para no invalidar el token que este mismo test sigue usando), y
    confirma que desactivarse a SÍ MISMO se rechaza por LAST_ADMIN. Actúa
    como sí mismo (acting_user_id == target_id) porque ADMIN_IMMUTABLE ya
    bloquearía cualquier intento de un admin sobre OTRO admin antes de
    llegar a este chequeo (ver Tarea C)."""
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
            await user_service.set_user_active(db_session, target_id, False, target_id)
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
    """Mismo escenario que arriba pero degradando el propio rol (admin ->
    analista) en vez de desactivar."""
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
            await user_service.update_user_role(db_session, target_id, "analista", target_id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "LAST_ADMIN"
    finally:
        for uid in other_ids:
            await db_session.execute(text("UPDATE users SET is_active = true WHERE user_id = :id"), {"id": uid})
        if other_ids:
            await db_session.commit()
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


# ── Tarea C: un admin no puede modificar a OTRO admin ───────────────────────

async def test_admin_cannot_demote_another_admin(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    email = "pytest_admin_immutable_role@example.com"
    try:
        target_id = await _create_user(client, admin_token, email, "admin")

        resp = await client.patch(
            f"/api/v1/admin/users/{target_id}/role",
            json={"role": "analista"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "ADMIN_IMMUTABLE"
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


async def test_admin_cannot_deactivate_another_admin(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    email = "pytest_admin_immutable_status@example.com"
    try:
        target_id = await _create_user(client, admin_token, email, "admin")

        resp = await client.patch(
            f"/api/v1/admin/users/{target_id}/status",
            json={"is_active": False},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "ADMIN_IMMUTABLE"
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


async def test_admin_can_deactivate_analista_without_restriction(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """La gestión de analistas no cambia -- sin restricciones nuevas."""
    email = "pytest_analista_deactivate@example.com"
    try:
        target_id = await _create_user(client, admin_token, email, "analista")

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


async def test_admin_can_promote_analista_to_admin_without_restriction(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """ADMIN_IMMUTABLE mira el rol ACTUAL del objetivo (antes del cambio) --
    promover a un analista a admin no es "modificar a otro admin", así que
    sigue permitido."""
    email = "pytest_analista_promote@example.com"
    try:
        target_id = await _create_user(client, admin_token, email, "analista")

        resp = await client.patch(
            f"/api/v1/admin/users/{target_id}/role",
            json={"role": "admin"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


# ── Tarea D: no promover a admin una cuenta desactivada (admin-fantasma) ────

async def test_promote_deactivated_analista_to_admin_is_rejected(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """Evita el admin-fantasma: promover una cuenta inactiva a admin la
    dejaría atrapada (admin + inactiva, y ADMIN_IMMUTABLE bloquearía a
    cualquier otro admin que intente reactivarla)."""
    email = "pytest_promote_inactive@example.com"
    try:
        target_id = await _create_user(client, admin_token, email, "analista")

        deactivate_resp = await client.patch(
            f"/api/v1/admin/users/{target_id}/status",
            json={"is_active": False},
            headers=auth_headers(admin_token),
        )
        assert deactivate_resp.status_code == 200

        promote_resp = await client.patch(
            f"/api/v1/admin/users/{target_id}/role",
            json={"role": "admin"},
            headers=auth_headers(admin_token),
        )
        assert promote_resp.status_code == 409
        assert promote_resp.json()["detail"]["code"] == "INACTIVE_CANNOT_PROMOTE"
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


async def test_create_new_admin_still_allowed(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """La regla es sobre MODIFICAR admins existentes, no sobre crearlos."""
    email = "pytest_create_admin_allowed@example.com"
    try:
        resp = await client.post(
            "/api/v1/admin/users",
            json={"email": email, "password": _VALID_PASSWORD, "role": "admin"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "admin"
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()
