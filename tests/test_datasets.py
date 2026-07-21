"""
tests/test_datasets.py
Pruebas de ingesta de datasets — upload, listado, validación de acceso.
Requiere la base de datos de desarrollo con el usuario admin seed.
"""
import io

import pytest
from httpx import AsyncClient

from tests.conftest import TEST_FILE_PREFIX, auth_headers

pytestmark = pytest.mark.asyncio

_VALID_CSV = (
    "latitude,longitude,district_id,source,point_type,verified\n"
    "-12.1191,-77.0273,15,MINAM,contenedor,true\n"
    "-12.0464,-77.0428,1,SERPAR,punto_verde,false\n"
).encode("utf-8")


async def test_list_datasets_requires_auth(client: AsyncClient):
    """GET /datasets sin token retorna 401 o 403 (sin autenticación)."""
    resp = await client.get("/api/v1/datasets")
    assert resp.status_code in (401, 403)


async def test_list_datasets_with_token(client: AsyncClient, admin_token: str):
    """GET /datasets con token válido retorna lista (puede estar vacía)."""
    resp = await client.get(
        "/api/v1/datasets",
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_upload_unsupported_format(client: AsyncClient, admin_token: str):
    """POST /datasets con archivo .txt retorna 422."""
    resp = await client.post(
        "/api/v1/datasets",
        files={"file": ("test.txt", io.BytesIO(b"texto plano"), "text/plain")},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 422


async def test_upload_valid_csv(
    client: AsyncClient, admin_token: str, cleanup_datasets: list[int]
):
    """POST /datasets con CSV válido retorna 201 con dataset_id y row_count.

    El archivo se nombra con TEST_FILE_PREFIX y el dataset creado se registra
    en `cleanup_datasets` para que el fixture lo borre de Neon al terminar
    (ver tests/conftest.py — crítico #1 del inventario de faltantes).
    """
    resp = await client.post(
        "/api/v1/datasets",
        files={
            "file": (
                f"{TEST_FILE_PREFIX}puntos.csv",
                io.BytesIO(_VALID_CSV),
                "text/csv",
            )
        },
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "dataset_id" in data
    assert data["row_count"] == 2
    assert data["status"] == "pending"

    cleanup_datasets.append(data["dataset_id"])


async def test_upload_requires_auth(client: AsyncClient):
    """POST /datasets sin token retorna 401 o 403 (sin autenticación)."""
    resp = await client.post(
        "/api/v1/datasets",
        files={"file": ("test.csv", io.BytesIO(_VALID_CSV), "text/csv")},
    )
    assert resp.status_code in (401, 403)
