"""
tests/test_dataset_delete.py
DELETE /api/v1/datasets/{id} -- elimina un dataset COMPLETO (archivo +
registro), no solo filas dentro de él. Cubre:
  - Un dataset no comprometido se borra (archivo + registro).
  - Un dataset 'committed' se rechaza con 409 DATASET_COMMITTED (inmutable).
  - 404 si el dataset no existe.
  - audit_log: las entradas previas del dataset NO se borran (quedan con
    dataset_id=NULL por el ON DELETE SET NULL de la FK), y la propia acción
    de borrado queda registrada con el dataset_id original conservado en
    `details`.
"""
import io
import json

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TEST_FILE_PREFIX, auth_headers

pytestmark = pytest.mark.asyncio

_TEST_SOURCE = "pytest_dataset_delete"


def _csv(rows: str) -> bytes:
    return ("latitude,longitude,district_id,source\n" + rows).encode("utf-8")


@pytest_asyncio.fixture
async def cleanup_recycling_points(db_session: AsyncSession):
    yield
    await db_session.execute(text("DELETE FROM recycling_points WHERE source = :s"), {"s": _TEST_SOURCE})
    await db_session.commit()


async def _upload(client: AsyncClient, admin_token: str, raw: bytes, name: str = "test.csv") -> int:
    resp = await client.post(
        "/api/v1/datasets",
        files={"file": (f"{TEST_FILE_PREFIX}{name}", io.BytesIO(raw), "text/csv")},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["dataset_id"]


async def test_delete_non_committed_dataset_succeeds(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    dataset_id = await _upload(client, admin_token, _csv(f"-12.05,-77.02,150101,{_TEST_SOURCE}\n"))

    resp = await client.delete(f"/api/v1/datasets/{dataset_id}", headers=auth_headers(admin_token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] is True
    assert data["dataset_id"] == dataset_id

    # Ya no existe -- otra operación sobre el mismo id debe dar 404.
    validate_resp = await client.get(
        f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token)
    )
    assert validate_resp.status_code == 404

    row = (await db_session.execute(
        text("SELECT dataset_id FROM datasets WHERE dataset_id = :id"), {"id": dataset_id}
    )).first()
    assert row is None

    # Limpieza de la propia entrada "delete_dataset" que este test generó --
    # su dataset_id quedó NULL (SET NULL), así que se identifica por details.
    await db_session.execute(
        text("DELETE FROM audit_log WHERE action = 'delete_dataset' AND (details::jsonb ->> 'dataset_id')::int = :id"),
        {"id": dataset_id},
    )
    await db_session.commit()


async def test_delete_committed_dataset_is_rejected(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, cleanup_recycling_points
):
    dataset_id = await _upload(client, admin_token, _csv(f"-12.05,-77.02,150101,{_TEST_SOURCE}\n"))
    try:
        validate_resp = await client.get(
            f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token)
        )
        assert validate_resp.json()["valid"] is True

        commit_resp = await client.patch(
            f"/api/v1/datasets/{dataset_id}",
            json={"status": "committed"},
            headers=auth_headers(admin_token),
        )
        assert commit_resp.status_code == 200

        resp = await client.delete(f"/api/v1/datasets/{dataset_id}", headers=auth_headers(admin_token))
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "DATASET_COMMITTED"

        row = (await db_session.execute(
            text("SELECT status FROM datasets WHERE dataset_id = :id"), {"id": dataset_id}
        )).first()
        assert row is not None
        assert row[0] == "committed"
    finally:
        await db_session.execute(text("DELETE FROM audit_log WHERE dataset_id = :id"), {"id": dataset_id})
        await db_session.execute(text("DELETE FROM datasets WHERE dataset_id = :id"), {"id": dataset_id})
        await db_session.commit()


async def test_delete_nonexistent_dataset_returns_404(client: AsyncClient, admin_token: str):
    resp = await client.delete("/api/v1/datasets/999999999", headers=auth_headers(admin_token))
    assert resp.status_code == 404


async def test_delete_preserves_prior_audit_log_as_orphaned_and_logs_the_deletion(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    dataset_id = await _upload(client, admin_token, _csv(f"-12.05,-77.02,150101,{_TEST_SOURCE}\n"))
    await client.get(f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token))

    prior_ids = [row[0] for row in (await db_session.execute(
        text("SELECT audit_id FROM audit_log WHERE dataset_id = :id"), {"id": dataset_id}
    )).fetchall()]
    assert len(prior_ids) == 2  # upload + validate

    resp = await client.delete(f"/api/v1/datasets/{dataset_id}", headers=auth_headers(admin_token))
    assert resp.status_code == 200

    delete_audit_id = None
    try:
        # Las entradas previas SIGUEN existiendo -- solo dataset_id quedó NULL
        # (ON DELETE SET NULL), no se borraron junto con el dataset.
        rows = (await db_session.execute(
            text("SELECT audit_id, dataset_id FROM audit_log WHERE audit_id = ANY(:ids)"),
            {"ids": prior_ids},
        )).fetchall()
        assert len(rows) == len(prior_ids)
        for _audit_id, ds_id in rows:
            assert ds_id is None

        # La propia acción de borrado quedó registrada. Su columna dataset_id
        # también termina en NULL por el mismo mecanismo, pero el dataset_id
        # original sobrevive dentro de `details`.
        delete_log = (await db_session.execute(
            text(
                "SELECT audit_id, dataset_id, details FROM audit_log "
                "WHERE action = 'delete_dataset' ORDER BY audit_id DESC LIMIT 1"
            )
        )).first()
        assert delete_log is not None
        delete_audit_id, delete_dataset_id_col, details_raw = delete_log
        assert delete_dataset_id_col is None
        details = json.loads(details_raw)
        assert details["dataset_id"] == dataset_id
        assert details["filename"]
    finally:
        cleanup_ids = prior_ids + ([delete_audit_id] if delete_audit_id is not None else [])
        await db_session.execute(
            text("DELETE FROM audit_log WHERE audit_id = ANY(:ids)"), {"ids": cleanup_ids}
        )
        await db_session.commit()
