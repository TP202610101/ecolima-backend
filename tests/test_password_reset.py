"""
tests/test_password_reset.py

Historia "recuperar mi contraseña":
  - POST /api/v1/auth/forgot-password  {email}
  - POST /api/v1/auth/reset-password   {token, new_password}

Convenciones seguidas (ver tests/test_auth_hardening.py):
  - Usuarios de prueba se crean vía admin_token y se borran en el finally
    (el borrado en cascada se lleva también sus password_reset_tokens, ver
    ondelete="CASCADE" en el modelo).
  - El envío real de correo se mockea sobreescribiendo get_password_reset_sender
    con app.dependency_overrides -- no depende de un servidor SMTP real,
    igual que FakeMLApiClient en test_ml_service_proxy.py.
"""
import hashlib
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.password_reset_service import (
    MAX_RESET_REQUESTS,
    create_reset_token,
    get_password_reset_sender,
)
from main import app
from tests.conftest import auth_headers

STRONG_PASSWORD = "NuevaPasswordValida123!"


@pytest_asyncio.fixture
async def fake_email_sender():
    """Captura los correos que el endpoint 'enviaría', sin tocar SMTP real."""
    sent: list[dict] = []

    async def _fake(to_email: str, reset_url: str) -> None:
        sent.append({"to": to_email, "url": reset_url})

    app.dependency_overrides[get_password_reset_sender] = lambda: _fake
    yield sent
    app.dependency_overrides.pop(get_password_reset_sender, None)


def _extract_token(reset_url: str) -> str:
    query = parse_qs(urlparse(reset_url).query)
    return query["token"][0]


@pytest_asyncio.fixture
async def test_user(client: AsyncClient, admin_token: str, db_session: AsyncSession):
    """Usuario analista de prueba, activo, con contraseña conocida."""
    email = "pytest_password_reset_target@example.com"
    password = "PasswordOriginal123!"
    try:
        resp = await client.post(
            "/api/v1/admin/users",
            json={"email": email, "password": password, "role": "analista"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 201, resp.text
        yield {"user_id": resp.json()["user_id"], "email": email, "password": password}
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


# ── Respuesta neutra ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forgot_password_response_identical_for_existing_inexistent_and_inactive(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, fake_email_sender: list
):
    inactive_email = "pytest_password_reset_inactive@example.com"
    create_resp = await client.post(
        "/api/v1/admin/users",
        json={"email": inactive_email, "password": "PasswordValida123!", "role": "analista"},
        headers=auth_headers(admin_token),
    )
    assert create_resp.status_code == 201
    inactive_user_id = create_resp.json()["user_id"]
    await client.patch(
        f"/api/v1/admin/users/{inactive_user_id}/status",
        json={"is_active": False},
        headers=auth_headers(admin_token),
    )

    try:
        from app.core.config import Settings
        settings = Settings()

        resp_existing = await client.post(
            "/api/v1/auth/forgot-password", json={"email": settings.admin_email}
        )
        resp_missing = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "pytest-no-existe-password-reset@example.com"},
        )
        resp_inactive = await client.post(
            "/api/v1/auth/forgot-password", json={"email": inactive_email}
        )

        assert resp_existing.status_code == resp_missing.status_code == resp_inactive.status_code == 202
        assert resp_existing.json() == resp_missing.json() == resp_inactive.json()

        # Solo se "envió" correo para la cuenta real y activa.
        assert len(fake_email_sender) == 1
        assert fake_email_sender[0]["to"] == settings.admin_email
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": inactive_email})
        await db_session.commit()


# ── Flujo feliz ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reset_password_with_valid_token_changes_password_and_invalidates_old_jwt(
    client: AsyncClient, test_user: dict, fake_email_sender: list
):
    login_resp = await client.post(
        "/api/v1/auth/login", json={"email": test_user["email"], "password": test_user["password"]}
    )
    assert login_resp.status_code == 200
    old_token = login_resp.json()["access_token"]

    forgot_resp = await client.post("/api/v1/auth/forgot-password", json={"email": test_user["email"]})
    assert forgot_resp.status_code == 202
    assert len(fake_email_sender) == 1
    raw_token = _extract_token(fake_email_sender[0]["url"])

    reset_resp = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw_token, "new_password": STRONG_PASSWORD}
    )
    assert reset_resp.status_code == 200

    # El JWT emitido antes del cambio ya no sirve.
    check_resp = await client.get("/api/v1/map/districts", headers=auth_headers(old_token))
    assert check_resp.status_code == 401

    # La contraseña vieja ya no funciona, la nueva sí.
    old_login = await client.post(
        "/api/v1/auth/login", json={"email": test_user["email"], "password": test_user["password"]}
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login", json={"email": test_user["email"], "password": STRONG_PASSWORD}
    )
    assert new_login.status_code == 200


# ── Token inválido / vencido / ya usado / inexistente ───────────────────

@pytest.mark.asyncio
async def test_reset_password_rejects_nonexistent_token(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "este-token-nunca-existio", "new_password": STRONG_PASSWORD},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_RESET_TOKEN"


@pytest.mark.asyncio
async def test_reset_password_rejects_already_used_token(
    client: AsyncClient, test_user: dict, fake_email_sender: list
):
    forgot_resp = await client.post("/api/v1/auth/forgot-password", json={"email": test_user["email"]})
    assert forgot_resp.status_code == 202
    raw_token = _extract_token(fake_email_sender[0]["url"])

    first_use = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw_token, "new_password": STRONG_PASSWORD}
    )
    assert first_use.status_code == 200

    second_use = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw_token, "new_password": "OtraPasswordValida456!"}
    )
    assert second_use.status_code == 400
    assert second_use.json()["detail"]["code"] == "INVALID_RESET_TOKEN"


@pytest.mark.asyncio
async def test_reset_password_rejects_expired_token(
    client: AsyncClient, test_user: dict, db_session: AsyncSession, fake_email_sender: list
):
    forgot_resp = await client.post("/api/v1/auth/forgot-password", json={"email": test_user["email"]})
    assert forgot_resp.status_code == 202
    raw_token = _extract_token(fake_email_sender[0]["url"])
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    await db_session.execute(
        text("UPDATE password_reset_tokens SET expires_at = :past WHERE token_hash = :hash"),
        {"past": datetime.utcnow() - timedelta(minutes=1), "hash": token_hash},
    )
    await db_session.commit()

    resp = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw_token, "new_password": STRONG_PASSWORD}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_RESET_TOKEN"


@pytest.mark.asyncio
async def test_new_forgot_password_request_invalidates_previous_token(
    client: AsyncClient, test_user: dict, fake_email_sender: list
):
    first_resp = await client.post("/api/v1/auth/forgot-password", json={"email": test_user["email"]})
    assert first_resp.status_code == 202
    first_token = _extract_token(fake_email_sender[0]["url"])

    second_resp = await client.post("/api/v1/auth/forgot-password", json={"email": test_user["email"]})
    assert second_resp.status_code == 202
    second_token = _extract_token(fake_email_sender[1]["url"])

    assert first_token != second_token

    stale_attempt = await client.post(
        "/api/v1/auth/reset-password", json={"token": first_token, "new_password": STRONG_PASSWORD}
    )
    assert stale_attempt.status_code == 400
    assert stale_attempt.json()["detail"]["code"] == "INVALID_RESET_TOKEN"

    fresh_attempt = await client.post(
        "/api/v1/auth/reset-password", json={"token": second_token, "new_password": STRONG_PASSWORD}
    )
    assert fresh_attempt.status_code == 200


# ── Contraseña débil ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reset_password_rejects_weak_password_without_burning_the_token(
    client: AsyncClient, test_user: dict, fake_email_sender: list
):
    forgot_resp = await client.post("/api/v1/auth/forgot-password", json={"email": test_user["email"]})
    assert forgot_resp.status_code == 202
    raw_token = _extract_token(fake_email_sender[0]["url"])

    weak_attempt = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw_token, "new_password": "corta1"}
    )
    assert weak_attempt.status_code == 422
    assert weak_attempt.json()["detail"]["code"] == "WEAK_PASSWORD"

    # El token sigue vigente -- una contraseña débil no lo quema.
    retry = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw_token, "new_password": STRONG_PASSWORD}
    )
    assert retry.status_code == 200


# ── El token nunca se guarda en claro ──────────────────────────────────────

@pytest.mark.asyncio
async def test_token_is_never_stored_in_cleartext(
    client: AsyncClient, test_user: dict, db_session: AsyncSession, fake_email_sender: list
):
    forgot_resp = await client.post("/api/v1/auth/forgot-password", json={"email": test_user["email"]})
    assert forgot_resp.status_code == 202
    raw_token = _extract_token(fake_email_sender[0]["url"])

    row = (await db_session.execute(
        text("SELECT token_hash FROM password_reset_tokens WHERE user_id = :uid ORDER BY created_at DESC LIMIT 1"),
        {"uid": test_user["user_id"]},
    )).first()

    assert row is not None
    stored_hash = row[0]
    assert stored_hash != raw_token
    assert raw_token not in stored_hash
    assert len(stored_hash) == 64  # SHA-256 hex digest
    assert stored_hash == hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ── Límite de solicitudes por IP (igual mecanismo que /login) ────────────

@pytest.mark.asyncio
async def test_forgot_password_rate_limited_after_5_requests_per_minute(client: AsyncClient):
    responses = []
    for _ in range(6):
        resp = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "pytest-rate-limit-ip-test@example.com"},
        )
        responses.append(resp.status_code)

    assert responses[:5] == [202, 202, 202, 202, 202]
    assert responses[5] == 429


# ── Límite de solicitudes por correo ──────────────────────────────────────

@pytest.mark.asyncio
async def test_create_reset_token_enforces_per_user_request_limit(
    test_user: dict, db_session: AsyncSession
):
    """Prueba la capa de servicio directamente (sin pasar por el cliente
    HTTP) para no mezclar este límite por usuario con el límite por IP de
    slowapi, que aplica aparte sobre el mismo endpoint y con el mismo
    umbral -- ver test_forgot_password_rate_limited_after_5_requests_per_minute."""
    from app.models.user import User

    user = await User.get_by_id(db_session, test_user["user_id"])

    tokens = [await create_reset_token(db_session, user) for _ in range(MAX_RESET_REQUESTS)]
    assert all(tokens)

    one_more = await create_reset_token(db_session, user)
    assert one_more is None
