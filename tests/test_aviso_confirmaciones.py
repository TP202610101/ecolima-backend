"""
tests/test_aviso_confirmaciones.py

Historia "confirmación de lectura de avisos": cada cuenta (admin o analista)
confirma, por separado, haber leído cada uno de los tres modales de ayuda del
Análisis ("cómo se ordenan", "fuentes y limitaciones", "acerca del modelo").
Se confirma una vez por (clave, version); si la versión de una clave cambia,
esa clave vuelve a quedar pendiente -- las demás no se ven afectadas. El
backend nunca bloquea nada: solo guarda y consulta confirmaciones.

  - GET  /api/v1/me/avisos?clave=...&version=...
  - POST /api/v1/me/avisos/confirmar  {clave, version}
  - GET  /api/v1/admin/avisos/confirmaciones?clave=...&version=...  (admin)

Convención de fixtures igual que tests/test_password_reset.py: usuarios de
prueba creados vía admin_token, borrados en el finally (CASCADE se lleva
también sus aviso_confirmaciones).
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import auth_headers

CLAVE_A = "como-se-ordenan"
CLAVE_B = "fuentes-limitaciones"
CLAVE_C = "acerca-del-modelo"


@pytest_asyncio.fixture
async def analista(client: AsyncClient, admin_token: str, db_session: AsyncSession):
    email = "pytest_aviso_analista_a@example.com"
    password = "PasswordValida123!"
    try:
        resp = await client.post(
            "/api/v1/admin/users",
            json={"email": email, "password": password, "role": "analista"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 201, resp.text
        user_id = resp.json()["user_id"]

        login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200
        yield {"user_id": user_id, "email": email, "token": login.json()["access_token"]}
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


@pytest_asyncio.fixture
async def otro_analista(client: AsyncClient, admin_token: str, db_session: AsyncSession):
    email = "pytest_aviso_analista_b@example.com"
    password = "PasswordValida123!"
    try:
        resp = await client.post(
            "/api/v1/admin/users",
            json={"email": email, "password": password, "role": "analista"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 201, resp.text
        user_id = resp.json()["user_id"]

        login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200
        yield {"user_id": user_id, "email": email, "token": login.json()["access_token"]}
    finally:
        await db_session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await db_session.commit()


# ── Confirmar y consultar ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_estado_inicial_no_confirmado(client: AsyncClient, analista: dict):
    resp = await client.get(
        "/api/v1/me/avisos",
        params={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["confirmado"] is False
    assert body["confirmado_en"] is None


@pytest.mark.asyncio
async def test_confirmar_aviso_y_luego_get_refleja_confirmado(client: AsyncClient, analista: dict):
    confirm_resp = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["confirmado"] is True
    assert confirm_resp.json()["confirmado_en"] is not None

    get_resp = await client.get(
        "/api/v1/me/avisos",
        params={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["confirmado"] is True


# ── Las tres claves son independientes entre sí ────────────────────────────

@pytest.mark.asyncio
async def test_confirmar_una_clave_no_afecta_a_las_otras_dos(client: AsyncClient, analista: dict):
    confirm_a = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": CLAVE_A, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert confirm_a.status_code == 200

    for clave_pendiente in (CLAVE_B, CLAVE_C):
        resp = await client.get(
            "/api/v1/me/avisos",
            params={"clave": clave_pendiente, "version": "1"},
            headers=auth_headers(analista["token"]),
        )
        assert resp.status_code == 200
        assert resp.json()["confirmado"] is False

    # Las tres, confirmadas por separado, quedan todas en True -- no se pisan.
    for clave in (CLAVE_A, CLAVE_B, CLAVE_C):
        await client.post(
            "/api/v1/me/avisos/confirmar",
            json={"clave": clave, "version": "1"},
            headers=auth_headers(analista["token"]),
        )
    for clave in (CLAVE_A, CLAVE_B, CLAVE_C):
        resp = await client.get(
            "/api/v1/me/avisos",
            params={"clave": clave, "version": "1"},
            headers=auth_headers(analista["token"]),
        )
        assert resp.json()["confirmado"] is True


# ── Idempotencia ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_confirmar_dos_veces_es_idempotente_no_duplica_fila(
    client: AsyncClient, analista: dict, db_session: AsyncSession
):
    first = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert first.status_code == 200
    first_confirmado_en = first.json()["confirmado_en"]

    second = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert second.status_code == 200
    # No se actualiza confirmado_en en la segunda llamada -- la primera
    # confirmación es la que cuenta.
    assert second.json()["confirmado_en"] == first_confirmado_en

    count = (await db_session.execute(
        text(
            "SELECT COUNT(*) FROM aviso_confirmaciones "
            "WHERE user_id = :uid AND clave = :clave AND version = :version"
        ),
        {"uid": analista["user_id"], "clave": CLAVE_B, "version": "1"},
    )).scalar_one()
    assert count == 1


# ── Nueva versión vuelve a quedar pendiente ───────────────────────────────

@pytest.mark.asyncio
async def test_nueva_version_queda_pendiente_aunque_version_anterior_este_confirmada(
    client: AsyncClient, analista: dict
):
    confirm_v1 = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert confirm_v1.status_code == 200

    status_v2 = await client.get(
        "/api/v1/me/avisos",
        params={"clave": CLAVE_B, "version": "2"},
        headers=auth_headers(analista["token"]),
    )
    assert status_v2.status_code == 200
    assert status_v2.json()["confirmado"] is False

    confirm_v2 = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": CLAVE_B, "version": "2"},
        headers=auth_headers(analista["token"]),
    )
    assert confirm_v2.status_code == 200

    # La confirmación de la v1 sigue existiendo -- no se pierde ni se pisa.
    status_v1 = await client.get(
        "/api/v1/me/avisos",
        params={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert status_v1.status_code == 200
    assert status_v1.json()["confirmado"] is True


# ── Validación de clave y version ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_clave_desconocida_da_422_en_get(client: AsyncClient, analista: dict):
    resp = await client.get(
        "/api/v1/me/avisos",
        params={"clave": "aviso-que-no-existe", "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_clave_desconocida_da_422_en_post(client: AsyncClient, analista: dict):
    resp = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": "aviso-que-no-existe", "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_clave_desconocida_da_422_en_reporte_admin(client: AsyncClient, admin_token: str):
    resp = await client.get(
        "/api/v1/admin/avisos/confirmaciones",
        params={"clave": "aviso-que-no-existe", "version": "1"},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_version_vacia_da_422(client: AsyncClient, analista: dict):
    resp = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": CLAVE_A, "version": ""},
        headers=auth_headers(analista["token"]),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_version_de_mas_de_20_caracteres_da_422(client: AsyncClient, analista: dict):
    version_larga = "1" * 21
    resp = await client.get(
        "/api/v1/me/avisos",
        params={"clave": CLAVE_A, "version": version_larga},
        headers=auth_headers(analista["token"]),
    )
    assert resp.status_code == 422


# ── Autenticación ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sin_sesion_es_rechazado(client: AsyncClient):
    """Sin Authorization, HTTPBearer (igual que en el resto de la API) devuelve
    403 -- 401 queda para un token presente pero inválido/expirado. Cualquiera
    de los dos casos deja el recurso fuera de alcance sin sesión."""
    get_resp = await client.get("/api/v1/me/avisos", params={"clave": CLAVE_A, "version": "1"})
    assert get_resp.status_code == 403

    post_resp = await client.post(
        "/api/v1/me/avisos/confirmar", json={"clave": CLAVE_A, "version": "1"}
    )
    assert post_resp.status_code == 403

    invalid_token_resp = await client.get(
        "/api/v1/me/avisos",
        params={"clave": CLAVE_A, "version": "1"},
        headers=auth_headers("token-invalido"),
    )
    assert invalid_token_resp.status_code == 401


# ── Reporte admin ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reporte_confirmaciones_solo_para_admin(client: AsyncClient, analista: dict):
    resp = await client.get(
        "/api/v1/admin/avisos/confirmaciones",
        params={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reporte_tiene_la_forma_clave_version_usuarios_con_role(
    client: AsyncClient, admin_token: str, analista: dict, otro_analista: dict
):
    confirm_resp = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert confirm_resp.status_code == 200

    report_resp = await client.get(
        "/api/v1/admin/avisos/confirmaciones",
        params={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(admin_token),
    )
    assert report_resp.status_code == 200
    body = report_resp.json()
    assert body["clave"] == CLAVE_B
    assert body["version"] == "1"

    rows = {row["email"]: row for row in body["usuarios"]}
    assert rows[analista["email"]]["confirmado"] is True
    assert rows[analista["email"]]["confirmado_en"] is not None
    assert rows[analista["email"]]["role"] == "analista"
    assert rows[otro_analista["email"]]["confirmado"] is False
    assert rows[otro_analista["email"]]["confirmado_en"] is None


@pytest.mark.asyncio
async def test_reporte_excluye_ciudadanos_e_inactivos(
    client: AsyncClient, admin_token: str, analista: dict, otro_analista: dict, db_session: AsyncSession
):
    # Desactiva a otro_analista -- no debe aparecer en el reporte aunque
    # exista como cuenta.
    deactivate = await client.patch(
        f"/api/v1/admin/users/{otro_analista['user_id']}/status",
        json={"is_active": False},
        headers=auth_headers(admin_token),
    )
    assert deactivate.status_code == 200

    # "ciudadano" no se puede crear vía la API (ALLOWED_ROLES la rechaza) --
    # se inserta directo para probar que el reporte también la filtraría si
    # alguna vez existiera una fila así.
    await db_session.execute(
        text(
            "INSERT INTO users (email, hashed_password, full_name, role, is_active, token_version) "
            "VALUES (:email, 'x', 'Ciudadano de prueba', 'ciudadano', true, 0)"
        ),
        {"email": "pytest_aviso_ciudadano@example.com"},
    )
    await db_session.commit()

    try:
        report_resp = await client.get(
            "/api/v1/admin/avisos/confirmaciones",
            params={"clave": CLAVE_B, "version": "1"},
            headers=auth_headers(admin_token),
        )
        assert report_resp.status_code == 200
        emails = {row["email"] for row in report_resp.json()["usuarios"]}
        assert otro_analista["email"] not in emails
        assert "pytest_aviso_ciudadano@example.com" not in emails
        assert analista["email"] in emails
    finally:
        await db_session.execute(
            text("DELETE FROM users WHERE email = :email"),
            {"email": "pytest_aviso_ciudadano@example.com"},
        )
        await db_session.commit()


# ── Aislamiento entre usuarios ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_un_analista_no_puede_confirmar_ni_ver_el_estado_de_otro(
    client: AsyncClient, analista: dict, otro_analista: dict
):
    """No existe forma de pasar un user_id distinto en el payload -- el
    endpoint siempre usa get_current_user. Lo demostramos confirmando como A
    y verificando que B sigue sin confirmar."""
    confirm_resp = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert confirm_resp.status_code == 200

    status_b = await client.get(
        "/api/v1/me/avisos",
        params={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(otro_analista["token"]),
    )
    assert status_b.status_code == 200
    assert status_b.json()["confirmado"] is False

    # El payload tampoco acepta un campo para suplantar a otro usuario.
    confirm_with_extra_field = await client.post(
        "/api/v1/me/avisos/confirmar",
        json={"clave": CLAVE_B, "version": "1", "user_id": analista["user_id"]},
        headers=auth_headers(otro_analista["token"]),
    )
    assert confirm_with_extra_field.status_code == 200

    # Confirmó para B (el dueño del token), no para A -- A no se vio afectado.
    status_a_unchanged = await client.get(
        "/api/v1/me/avisos",
        params={"clave": CLAVE_B, "version": "1"},
        headers=auth_headers(analista["token"]),
    )
    assert status_a_unchanged.json()["confirmado_en"] == confirm_resp.json()["confirmado_en"]
