"""
tests/test_dataset_commit_concurrency.py
Auditoria de concurrencia, hallazgo #2: dos POST/PATCH de commit casi
simultaneos sobre el MISMO dataset podian correr el loop de insercion en
paralelo y duplicar recycling_points. El fix agrega un SELECT ... FOR UPDATE
sobre la fila del dataset antes de leer su status -- esto serializa
cualquier commit concurrente: el segundo espera el lock y, al obtenerlo, ve
status='committed' ya escrito por el primero.

Estos tests disparan concurrencia REAL (asyncio.gather con el cliente
ASGITransport + NullPool de conftest, que abre una conexion asyncpg nueva
por request) -- no una prueba secuencial, que no ejercita la race.
"""
import asyncio
import io

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TEST_FILE_PREFIX, auth_headers

pytestmark = pytest.mark.asyncio

_TEST_SOURCE = "pytest_commit_concurrency"


def _csv(rows: str) -> bytes:
    return ("latitude,longitude,district_id,source\n" + rows).encode("utf-8")


@pytest_asyncio.fixture
async def cleanup_recycling_points(db_session: AsyncSession):
    yield
    await db_session.execute(text("DELETE FROM recycling_points WHERE source = :s"), {"s": _TEST_SOURCE})
    await db_session.commit()


async def _upload_and_validate(client: AsyncClient, admin_token: str, raw: bytes) -> int:
    resp = await client.post(
        "/api/v1/datasets",
        files={"file": (f"{TEST_FILE_PREFIX}commit_concurrency.csv", io.BytesIO(raw), "text/csv")},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 201, resp.text
    dataset_id = resp.json()["dataset_id"]

    validate_resp = await client.get(
        f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token)
    )
    assert validate_resp.status_code == 200, validate_resp.text
    assert validate_resp.json()["valid"] is True

    return dataset_id


async def test_two_simultaneous_commits_only_one_succeeds_no_duplicate_points(
    client: AsyncClient,
    admin_token: str,
    cleanup_datasets: list[int],
    cleanup_recycling_points,
    db_session: AsyncSession,
):
    raw = _csv(
        f"-12.0501,-77.0201,150101,{_TEST_SOURCE}\n"
        f"-12.0602,-77.0302,150101,{_TEST_SOURCE}\n"
        f"-12.0703,-77.0403,150101,{_TEST_SOURCE}\n"
    )
    dataset_id = await _upload_and_validate(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)

    async def _commit():
        return await client.patch(
            f"/api/v1/datasets/{dataset_id}",
            json={"status": "committed"},
            headers=auth_headers(admin_token),
        )

    resp_a, resp_b = await asyncio.gather(_commit(), _commit())

    statuses = sorted([resp_a.status_code, resp_b.status_code])
    assert statuses == [200, 409], (
        f"Esperaba exactamente un 200 y un 409, obtuve {resp_a.status_code} y {resp_b.status_code}. "
        f"Bodies: {resp_a.text} | {resp_b.text}"
    )

    winner = resp_a if resp_a.status_code == 200 else resp_b
    loser = resp_b if resp_a.status_code == 200 else resp_a

    assert winner.json()["inserted"] == 3
    assert loser.json()["detail"]["code"] == "ALREADY_COMMITTED"

    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM recycling_points WHERE source = :s"), {"s": _TEST_SOURCE}
        )
    ).scalar_one()
    assert count == 3, f"Esperaba exactamente 3 puntos (sin duplicados), encontre {count}"

    status_row = (
        await db_session.execute(
            text("SELECT status FROM datasets WHERE dataset_id = :id"), {"id": dataset_id}
        )
    ).scalar_one()
    assert status_row == "committed"


async def test_lock_releases_after_a_rejected_commit_so_a_later_request_still_works(
    client: AsyncClient,
    admin_token: str,
    cleanup_datasets: list[int],
    cleanup_recycling_points,
    db_session: AsyncSession,
):
    """Confirma que el lock de FOR UPDATE se libera aun cuando la transaccion
    termina en un 409 (rollback), no solo en el camino feliz -- una peticion
    posterior (secuencial, no concurrente) debe poder leer el dataset sin
    quedar bloqueada para siempre."""
    raw = _csv(f"-12.0501,-77.0201,150101,{_TEST_SOURCE}\n")
    dataset_id = await _upload_and_validate(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)

    first = await client.patch(
        f"/api/v1/datasets/{dataset_id}",
        json={"status": "committed"},
        headers=auth_headers(admin_token),
    )
    assert first.status_code == 200

    # Segundo intento, ya secuencial (no concurrente) -- si el lock no se
    # hubiera liberado tras el primer commit, esta petición se colgaría en
    # vez de responder con 409 al instante.
    second = await asyncio.wait_for(
        client.patch(
            f"/api/v1/datasets/{dataset_id}",
            json={"status": "committed"},
            headers=auth_headers(admin_token),
        ),
        timeout=10,
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "ALREADY_COMMITTED"
