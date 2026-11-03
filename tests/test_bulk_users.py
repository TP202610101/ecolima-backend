"""
tests/test_bulk_users.py

Historia "creación de usuarios en bloque por CSV" (solo admin):
  - POST /api/v1/admin/users/bulk/validate   (no crea nada)
  - POST /api/v1/admin/users/bulk/confirm    (crea todo o nada)

Convenciones iguales a tests/test_password_reset.py:
  - El envío real de correo se mockea sobreescribiendo get_invitation_sender
    con app.dependency_overrides.
  - Usuarios de prueba se limpian en el finally; el borrado en cascada se
    lleva también sus password_reset_tokens.
"""
import hashlib
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import user_bulk_service
from app.services.password_reset_service import get_invitation_sender
from main import app
from tests.conftest import auth_headers

EMAIL_PREFIX = "pytest_bulk_"


def _csv(rows: str, header: str = "correo,nombre,rol\n") -> bytes:
    return (header + rows).encode("utf-8")


def _files(content: bytes, filename: str = "usuarios.csv"):
    return {"file": (filename, content, "text/csv")}


@pytest_asyncio.fixture
async def fake_invitation_sender():
    """Captura las invitaciones que el endpoint 'enviaría'. Si el correo
    está en `should_fail`, simula una falla real de envío (sin tocar SMTP)."""
    sent: list[dict] = []
    should_fail: set[str] = set()

    async def _fake(to_email: str, invite_url: str) -> None:
        if to_email in should_fail:
            raise RuntimeError("fallo de envío simulado")
        sent.append({"to": to_email, "url": invite_url})

    app.dependency_overrides[get_invitation_sender] = lambda: _fake
    yield sent, should_fail
    app.dependency_overrides.pop(get_invitation_sender, None)


@pytest_asyncio.fixture
async def analista(client: AsyncClient, admin_token: str, db_session: AsyncSession):
    email = f"{EMAIL_PREFIX}analista@example.com"
    password = "PasswordValida123!"
    try:
        resp = await client.post(
            "/api/v1/admin/users",
            json={"email": email, "password": password, "role": "analista"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 201, resp.text
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200
        yield {"email": email, "token": login.json()["access_token"]}
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


@pytest_asyncio.fixture
async def cleanup_bulk_users(db_session: AsyncSession):
    """Borra, al terminar el test, cualquier usuario cuyo correo empiece con
    EMAIL_PREFIX -- cubre tanto los creados explícitamente en el test como
    los que pudo haber creado un /bulk/confirm exitoso."""
    yield
    await db_session.execute(text("DELETE FROM users WHERE email LIKE :prefix"), {"prefix": f"{EMAIL_PREFIX}%"})
    await db_session.commit()


def _extract_token(invite_url: str) -> str:
    query = parse_qs(urlparse(invite_url).query)
    return query["token"][0]


# ── Validación: archivo válido / inválido ──────────────────────────────────

@pytest.mark.asyncio
async def test_valid_file_is_valid_and_creates_nothing(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, cleanup_bulk_users
):
    before = (await db_session.execute(
        text("SELECT COUNT(*) FROM users WHERE email LIKE :p"), {"p": f"{EMAIL_PREFIX}%"}
    )).scalar_one()

    content = _csv(f"{EMAIL_PREFIX}uno@example.com,Uno,analista\n{EMAIL_PREFIX}dos@example.com,,analista\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["total"] == 2
    assert [r["status"] for r in body["rows"]] == ["ok", "ok"]
    assert body["rows"][1]["full_name"] is None

    after = (await db_session.execute(
        text("SELECT COUNT(*) FROM users WHERE email LIKE :p"), {"p": f"{EMAIL_PREFIX}%"}
    )).scalar_one()
    assert after == before  # /validate nunca crea nada


@pytest.mark.asyncio
async def test_invalid_header_returns_422(client: AsyncClient, admin_token: str):
    content = _csv("x@example.com,X,analista\n", header="email,name,role\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "CABECERA_INVALIDA"


@pytest.mark.asyncio
async def test_empty_file_returns_422(client: AsyncClient, admin_token: str):
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(b""), headers=auth_headers(admin_token)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "ARCHIVO_VACIO"


@pytest.mark.asyncio
async def test_header_only_file_returns_422_archivo_vacio(client: AsyncClient, admin_token: str):
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(_csv("")), headers=auth_headers(admin_token)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "ARCHIVO_VACIO"


@pytest.mark.asyncio
async def test_more_than_200_rows_returns_422(client: AsyncClient, admin_token: str):
    rows = "".join(f"user{i}@example.com,U{i},analista\n" for i in range(201))
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(_csv(rows)), headers=auth_headers(admin_token)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "DEMASIADAS_FILAS"


@pytest.mark.asyncio
async def test_file_over_1mb_returns_422(client: AsyncClient, admin_token: str):
    padding = "x" * 2000
    rows = "".join(f"user{i}@example.com,{padding},analista\n" for i in range(600))
    content = _csv(rows)
    assert len(content) > 1 * 1024 * 1024
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "ARCHIVO_MUY_GRANDE"


# ── Validación: filas ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_email_marked_error(client: AsyncClient, admin_token: str):
    content = _csv("no-es-un-correo,Alguien,analista\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    body = resp.json()
    assert body["valid"] is False
    assert body["rows"][0]["errors"][0]["code"] == "EMAIL_INVALIDO"


@pytest.mark.asyncio
async def test_duplicate_email_in_file_marks_both_rows(client: AsyncClient, admin_token: str):
    content = _csv("dup@example.com,Uno,analista\ndup@example.com,Dos,analista\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    body = resp.json()
    assert body["valid"] is False
    codes = [[e["code"] for e in row["errors"]] for row in body["rows"]]
    assert codes == [["EMAIL_DUPLICADO_EN_ARCHIVO"], ["EMAIL_DUPLICADO_EN_ARCHIVO"]]


@pytest.mark.asyncio
async def test_email_already_registered_case_insensitive(client: AsyncClient, admin_token: str, analista: dict):
    upper_email = analista["email"].upper()
    content = _csv(f"{upper_email},Otro,analista\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    body = resp.json()
    assert body["valid"] is False
    assert body["rows"][0]["errors"][0]["code"] == "EMAIL_YA_REGISTRADO"


@pytest.mark.asyncio
async def test_admin_role_rejected(client: AsyncClient, admin_token: str):
    content = _csv("x@example.com,X,admin\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    body = resp.json()
    assert body["valid"] is False
    assert body["rows"][0]["errors"][0]["code"] == "ROL_NO_PERMITIDO"


@pytest.mark.asyncio
async def test_empty_role_rejected(client: AsyncClient, admin_token: str):
    content = _csv("x@example.com,X,\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    body = resp.json()
    assert body["valid"] is False
    assert body["rows"][0]["errors"][0]["code"] == "ROL_VACIO"


@pytest.mark.asyncio
async def test_semicolon_delimiter_supported(client: AsyncClient, admin_token: str):
    content = _csv("x@example.com;X;analista\n", header="correo;nombre;rol\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    body = resp.json()
    assert body["valid"] is True
    assert body["rows"][0]["email"] == "x@example.com"


@pytest.mark.asyncio
async def test_bom_is_accepted(client: AsyncClient, admin_token: str):
    content = "﻿correo,nombre,rol\nx@example.com,X,analista\n".encode("utf-8")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    body = resp.json()
    assert body["valid"] is True
    assert body["rows"][0]["email"] == "x@example.com"


@pytest.mark.asyncio
async def test_blank_rows_are_ignored(client: AsyncClient, admin_token: str):
    content = _csv("\n   \nx@example.com,X,analista\n,,\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["email"] == "x@example.com"


@pytest.mark.asyncio
async def test_line_numbers_match_the_file(client: AsyncClient, admin_token: str):
    # línea 1: cabecera: línea 2: dato; línea 3: en blanco; línea 4: dato.
    content = _csv("a@example.com,A,analista\n\nb@example.com,B,analista\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(admin_token)
    )
    body = resp.json()
    assert [r["line"] for r in body["rows"]] == [2, 4]


# ── Autorización ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analista_gets_403_on_validate(client: AsyncClient, analista: dict):
    content = _csv("x@example.com,X,analista\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/validate", files=_files(content), headers=auth_headers(analista["token"])
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_analista_gets_403_on_confirm(client: AsyncClient, analista: dict):
    content = _csv("x@example.com,X,analista\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/confirm", files=_files(content), headers=auth_headers(analista["token"])
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_no_token_is_rejected_like_the_rest_of_the_api(client: AsyncClient):
    content = _csv("x@example.com,X,analista\n")
    resp = await client.post("/api/v1/admin/users/bulk/validate", files=_files(content))
    assert resp.status_code == 403  # HTTPBearer sin header -- mismo código que el resto de la API


# ── Confirmación: crea todo o nada ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_confirm_creates_all_accounts_active_analista(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, fake_invitation_sender, cleanup_bulk_users
):
    sent, _ = fake_invitation_sender
    content = _csv(f"{EMAIL_PREFIX}a@example.com,A,analista\n{EMAIL_PREFIX}b@example.com,B,analista\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/confirm", files=_files(content), headers=auth_headers(admin_token)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["created"] == 2
    assert body["invitations_sent"] == 2
    assert body["invitations_failed"] == []
    assert len(sent) == 2

    rows = (await db_session.execute(
        text("SELECT email, role, is_active FROM users WHERE email LIKE :p ORDER BY email"),
        {"p": f"{EMAIL_PREFIX}%"},
    )).all()
    assert [(r.role, r.is_active) for r in rows] == [("analista", True), ("analista", True)]


@pytest.mark.asyncio
async def test_confirm_with_one_invalid_row_creates_nothing(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, fake_invitation_sender, cleanup_bulk_users
):
    content = _csv(f"{EMAIL_PREFIX}ok@example.com,OK,analista\n{EMAIL_PREFIX}bad@example.com,BAD,admin\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/confirm", files=_files(content), headers=auth_headers(admin_token)
    )
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body["valid"] is False
    assert any(row["errors"] for row in body["rows"])

    count = (await db_session.execute(
        text("SELECT COUNT(*) FROM users WHERE email LIKE :p"), {"p": f"{EMAIL_PREFIX}%"}
    )).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_created_account_cannot_login_without_a_known_password(
    client: AsyncClient, admin_token: str, fake_invitation_sender, cleanup_bulk_users
):
    content = _csv(f"{EMAIL_PREFIX}nopass@example.com,Nadie,analista\n")
    confirm = await client.post(
        "/api/v1/admin/users/bulk/confirm", files=_files(content), headers=auth_headers(admin_token)
    )
    assert confirm.status_code == 201

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": f"{EMAIL_PREFIX}nopass@example.com", "password": "CualquierCosa123!"},
    )
    assert login.status_code == 401


@pytest.mark.asyncio
async def test_invitation_token_sets_password_via_reset_password_and_is_single_use(
    client: AsyncClient, admin_token: str, fake_invitation_sender, cleanup_bulk_users
):
    sent, _ = fake_invitation_sender
    email = f"{EMAIL_PREFIX}invite@example.com"
    content = _csv(f"{email},Invitado,analista\n")
    confirm = await client.post(
        "/api/v1/admin/users/bulk/confirm", files=_files(content), headers=auth_headers(admin_token)
    )
    assert confirm.status_code == 201
    assert len(sent) == 1
    raw_token = _extract_token(sent[0]["url"])

    new_password = "PasswordNueva123!"
    reset = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw_token, "new_password": new_password}
    )
    assert reset.status_code == 200

    login = await client.post("/api/v1/auth/login", json={"email": email, "password": new_password})
    assert login.status_code == 200

    # El mismo token ya no sirve de nuevo (de un solo uso).
    reuse = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw_token, "new_password": "OtraPassword123!"}
    )
    assert reuse.status_code == 400


@pytest.mark.asyncio
async def test_invitation_token_expires_in_48_hours_not_10_minutes(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, fake_invitation_sender, cleanup_bulk_users
):
    sent, _ = fake_invitation_sender
    email = f"{EMAIL_PREFIX}ttl@example.com"
    content = _csv(f"{email},TTL,analista\n")
    confirm = await client.post(
        "/api/v1/admin/users/bulk/confirm", files=_files(content), headers=auth_headers(admin_token)
    )
    assert confirm.status_code == 201
    raw_token = _extract_token(sent[0]["url"])
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    expires_at = (await db_session.execute(
        text("SELECT expires_at FROM password_reset_tokens WHERE token_hash = :h"), {"h": token_hash}
    )).scalar_one()
    remaining = expires_at - datetime.utcnow()
    assert timedelta(hours=47) < remaining <= timedelta(hours=48)


@pytest.mark.asyncio
async def test_failed_invitation_email_is_reported_without_undoing_the_account(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, fake_invitation_sender, cleanup_bulk_users
):
    sent, should_fail = fake_invitation_sender
    fails_email = f"{EMAIL_PREFIX}failmail@example.com"
    ok_email = f"{EMAIL_PREFIX}okmail@example.com"
    should_fail.add(fails_email)

    content = _csv(f"{fails_email},Fails,analista\n{ok_email},OK,analista\n")
    resp = await client.post(
        "/api/v1/admin/users/bulk/confirm", files=_files(content), headers=auth_headers(admin_token)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["created"] == 2
    assert body["invitations_sent"] == 1
    assert body["invitations_failed"] == [{"email": fails_email, "line": 2}]

    count = (await db_session.execute(
        text("SELECT COUNT(*) FROM users WHERE email IN (:a, :b)"), {"a": fails_email, "b": ok_email}
    )).scalar_one()
    assert count == 2  # la cuenta de fails_email se creó igual -- el correo no la deshace


@pytest.mark.asyncio
async def test_failed_transaction_leaves_no_accounts_created(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, fake_invitation_sender,
    cleanup_bulk_users, monkeypatch,
):
    calls = {"n": 0}
    original = user_bulk_service.create_invitation_token

    async def _falla_en_la_segunda(db, user):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("fallo inducido para probar el rollback")
        return await original(db, user)

    monkeypatch.setattr(user_bulk_service, "create_invitation_token", _falla_en_la_segunda)

    content = _csv(
        f"{EMAIL_PREFIX}rollback1@example.com,Uno,analista\n"
        f"{EMAIL_PREFIX}rollback2@example.com,Dos,analista\n"
    )
    resp = await client.post(
        "/api/v1/admin/users/bulk/confirm", files=_files(content), headers=auth_headers(admin_token)
    )
    assert resp.status_code == 500

    count = (await db_session.execute(
        text("SELECT COUNT(*) FROM users WHERE email LIKE :p"), {"p": f"{EMAIL_PREFIX}%"}
    )).scalar_one()
    assert count == 0
