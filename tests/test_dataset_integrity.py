"""
tests/test_dataset_integrity.py
Cierra los 3 agujeros de integridad del flujo de datasets identificados en
auditoria-flujo-datasets.md:
  1. El commit ahora exige status == 'valid' (y re-chequea rango geográfico).
  2. Editar/borrar filas de un dataset 'committed' ahora se rechaza (409).
  3. /validate ahora rechaza district_id/source nulos y reporta duplicados
     (advertencia, no bloqueante).
"""
import io

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TEST_FILE_PREFIX, auth_headers

pytestmark = pytest.mark.asyncio

_TEST_SOURCE = "pytest_dataset_integrity"


def _csv(rows: str) -> bytes:
    return ("latitude,longitude,district_id,source\n" + rows).encode("utf-8")


@pytest_asyncio.fixture
async def cleanup_recycling_points(db_session: AsyncSession):
    """Borra los recycling_points que un commit exitoso haya insertado durante
    el test (cleanup_datasets no los toca -- solo borra el dataset/archivo)."""
    yield
    # LIKE con prefijo, no igualdad exacta -- algunos tests (ej. el de source
    # largo) insertan un source que EMPIEZA con _TEST_SOURCE pero le agrega
    # texto extra; con igualdad exacta esas filas quedaban huérfanas y
    # contaminaban tests siguientes que reusan las mismas coordenadas.
    await db_session.execute(text("DELETE FROM recycling_points WHERE source LIKE :s"), {"s": f"{_TEST_SOURCE}%"})
    await db_session.commit()


async def _upload(client: AsyncClient, admin_token: str, raw: bytes, name: str = "test.csv") -> int:
    resp = await client.post(
        "/api/v1/datasets",
        files={"file": (f"{TEST_FILE_PREFIX}{name}", io.BytesIO(raw), "text/csv")},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["dataset_id"]


# ── Flujo feliz (no debe romperse) ──────────────────────────────────────────

async def test_happy_path_pending_validate_commit_still_works(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int], cleanup_recycling_points
):
    raw = _csv(
        "-12.0501,-77.0201,150101,pytest_dataset_integrity\n"
        "-12.0602,-77.0302,150101,pytest_dataset_integrity\n"
    )
    dataset_id = await _upload(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)

    validate_resp = await client.get(
        f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token)
    )
    assert validate_resp.status_code == 200
    vdata = validate_resp.json()
    assert vdata["valid"] is True
    assert vdata["duplicate_rows"] == []

    commit_resp = await client.patch(
        f"/api/v1/datasets/{dataset_id}",
        json={"status": "committed"},
        headers=auth_headers(admin_token),
    )
    assert commit_resp.status_code == 200
    cdata = commit_resp.json()
    assert cdata["inserted"] == 2
    assert cdata["errors"] == []


# ── Hueco #1: commit exige status == 'valid' ────────────────────────────────

async def test_commit_rejected_when_dataset_never_validated(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int]
):
    raw = _csv("-12.05,-77.02,150101,pytest_dataset_integrity\n")
    dataset_id = await _upload(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)

    commit_resp = await client.patch(
        f"/api/v1/datasets/{dataset_id}",
        json={"status": "committed"},
        headers=auth_headers(admin_token),
    )
    assert commit_resp.status_code == 409
    assert commit_resp.json()["detail"]["code"] == "NOT_VALIDATED"


async def test_commit_rejected_when_dataset_is_invalid(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int]
):
    raw = (
        "latitude,longitude,district_id\n"  # falta 'source' -> invalid
        "-12.05,-77.02,150101\n"
    ).encode("utf-8")
    dataset_id = await _upload(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)

    validate_resp = await client.get(
        f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token)
    )
    assert validate_resp.json()["valid"] is False

    commit_resp = await client.patch(
        f"/api/v1/datasets/{dataset_id}",
        json={"status": "committed"},
        headers=auth_headers(admin_token),
    )
    assert commit_resp.status_code == 409
    assert commit_resp.json()["detail"]["code"] == "NOT_VALIDATED"


async def test_commit_rechecks_geographic_range_in_depth(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, cleanup_datasets: list[int]
):
    """Simula un dataset marcado 'valid' con una fila fuera de Lima (esto no
    debería poder pasar por /validate real, pero el commit igual debe
    blindarse -- defensa en profundidad, no confía ciegamente en el flag)."""
    raw = _csv("40.7,-74.0,150101,pytest_dataset_integrity\n")  # Nueva York
    dataset_id = await _upload(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)

    await db_session.execute(
        text("UPDATE datasets SET status = 'valid' WHERE dataset_id = :id"), {"id": dataset_id}
    )
    await db_session.commit()

    commit_resp = await client.patch(
        f"/api/v1/datasets/{dataset_id}",
        json={"status": "committed"},
        headers=auth_headers(admin_token),
    )
    assert commit_resp.status_code == 200
    data = commit_resp.json()
    assert data["inserted"] == 0
    assert any("rango" in e.get("error", "") for e in data["errors"])


# ── Hueco #2: dataset 'committed' es inmutable ──────────────────────────────

async def _upload_validate_commit(client: AsyncClient, admin_token: str, cleanup_datasets: list[int]) -> int:
    raw = _csv("-12.0501,-77.0201,150101,pytest_dataset_integrity\n")
    dataset_id = await _upload(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)
    await client.get(f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token))
    commit_resp = await client.patch(
        f"/api/v1/datasets/{dataset_id}", json={"status": "committed"}, headers=auth_headers(admin_token)
    )
    assert commit_resp.status_code == 200
    return dataset_id


async def test_edit_cells_rejected_on_committed_dataset(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int], cleanup_recycling_points
):
    dataset_id = await _upload_validate_commit(client, admin_token, cleanup_datasets)

    resp = await client.patch(
        f"/api/v1/datasets/{dataset_id}/cells",
        json={"edits": [{"row_index": 0, "column": "source", "new_value": "otro"}]},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "DATASET_COMMITTED"


async def test_delete_rows_rejected_on_committed_dataset(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int], cleanup_recycling_points
):
    dataset_id = await _upload_validate_commit(client, admin_token, cleanup_datasets)

    resp = await client.request(
        "DELETE",
        f"/api/v1/datasets/{dataset_id}/rows?confirm=true",
        json={"row_indices": [0], "reason": "test"},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "DATASET_COMMITTED"


async def test_delete_incomplete_rows_rejected_on_committed_dataset(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int], cleanup_recycling_points
):
    dataset_id = await _upload_validate_commit(client, admin_token, cleanup_datasets)

    resp = await client.request(
        "DELETE",
        f"/api/v1/datasets/{dataset_id}/rows?status=incomplete",
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "DATASET_COMMITTED"


# ── Hueco #3: validaciones faltantes ────────────────────────────────────────

async def test_validate_rejects_null_district_id_and_source(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int]
):
    raw = _csv(
        "-12.05,-77.02,,pytest_dataset_integrity\n"   # district_id vacío
        "-12.06,-77.03,150101,\n"                       # source vacío
    )
    dataset_id = await _upload(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)

    resp = await client.get(f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    columns_with_errors = {e["column"] for e in data["type_errors"]}
    assert "district_id" in columns_with_errors
    assert "source" in columns_with_errors


async def test_validate_reports_duplicate_coordinates_as_warning_not_blocking(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int]
):
    raw = _csv(
        "-12.0501,-77.0201,150101,pytest_dataset_integrity\n"
        "-12.0501,-77.0201,150101,pytest_dataset_integrity\n"  # misma coordenada exacta
    )
    dataset_id = await _upload(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)

    resp = await client.get(f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token))
    assert resp.status_code == 200
    data = resp.json()

    assert data["valid"] is True  # advertencia, no bloquea
    assert len(data["duplicate_rows"]) == 1
    assert sorted(data["duplicate_rows"][0]["row_indices"]) == [0, 1]


# ── Regresión: source largo rompía el commit sin que /validate avisara ──────
# dataset_id=26 (60 puntos reales de reciclaje) falló en producción porque
# "source" podía superar los 50 caracteres de la columna (VARCHAR) y
# /validate no lo detectaba -- el INSERT fallaba recién al comprometer, con
# StringDataRightTruncationError, y el frontend lo mostraba como "Sin
# conexión". Fix: VARCHAR(50) -> VARCHAR(150) + chequeo de longitud en
# _validate_types tomado del modelo (no hardcodeado).

async def test_validate_rejects_source_longer_than_column_limit(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int]
):
    too_long_source = "R" * 151  # 1 por encima del nuevo límite (VARCHAR(150))
    raw = _csv(f"-12.05,-77.02,150101,{too_long_source}\n")
    dataset_id = await _upload(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)

    resp = await client.get(f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token))
    assert resp.status_code == 200
    data = resp.json()

    assert data["valid"] is False
    source_errors = [e for e in data["type_errors"] if e["column"] == "source"]
    assert len(source_errors) == 1
    assert "150" in source_errors[0]["error"]


async def test_commit_succeeds_with_a_realistic_long_source_within_new_limit(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int], cleanup_recycling_points
):
    """El caso real que rompió dataset_id=26: una fuente tipo
    'Real - Santiago de Surco - prensa_verificar - Perú21 (...)' (88
    caracteres) -- entraba en VARCHAR(50), ahora entra cómodo en 150."""
    long_realistic_source = (
        f"{_TEST_SOURCE} - Santiago de Surco - prensa_verificar - "
        "Perú21 (comunicado Municipalidad de Surco)"
    )
    assert 50 < len(long_realistic_source) <= 150
    raw = _csv(f"-12.0501,-77.0201,150101,{long_realistic_source}\n")
    dataset_id = await _upload(client, admin_token, raw)
    cleanup_datasets.append(dataset_id)

    validate_resp = await client.get(f"/api/v1/datasets/{dataset_id}/validate", headers=auth_headers(admin_token))
    assert validate_resp.status_code == 200
    assert validate_resp.json()["valid"] is True

    commit_resp = await client.patch(
        f"/api/v1/datasets/{dataset_id}", json={"status": "committed"}, headers=auth_headers(admin_token)
    )
    assert commit_resp.status_code == 200
    assert commit_resp.json()["inserted"] == 1
