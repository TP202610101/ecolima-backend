"""
tests/test_ml.py
Pruebas de GET /ml/recommendations — filtro income_stratum (multi-valor).
Requiere la base de datos de desarrollo con el usuario admin seed.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_recommendations_without_income_stratum(client: AsyncClient, admin_token: str):
    """Sin el param, la llamada funciona igual que antes del cambio."""
    resp = await client.get(
        "/api/v1/ml/recommendations",
        params={"limit": 500},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "FeatureCollection"
    assert isinstance(data["features"], list)


async def test_recommendations_single_income_stratum(client: AsyncClient, admin_token: str):
    """Un solo valor: todas las zonas devueltas tienen ese estrato (o NULL)."""
    resp = await client.get(
        "/api/v1/ml/recommendations",
        params={"income_stratum": 3, "limit": 500},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    for feature in data["features"]:
        stratum = feature["properties"]["income_stratum"]
        assert stratum in (3, None)


async def test_recommendations_multiple_income_stratum(client: AsyncClient, admin_token: str):
    """Múltiples valores (repeated param): todas las zonas están en el conjunto (o NULL)."""
    resp = await client.get(
        "/api/v1/ml/recommendations",
        params={"income_stratum": [3, 4], "limit": 500},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    for feature in data["features"]:
        stratum = feature["properties"]["income_stratum"]
        assert stratum in (3, 4, None)


async def test_recommendations_income_stratum_out_of_range(client: AsyncClient, admin_token: str):
    """Valor fuera de {1..5} retorna 422, no un error de servidor."""
    resp = await client.get(
        "/api/v1/ml/recommendations",
        params={"income_stratum": 9},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 422


async def test_recommendations_income_stratum_with_no_matches_is_empty(
    client: AsyncClient, admin_token: str
):
    """
    Con los datos actuales, los estratos 1 y 2 no tienen ninguna zona
    is_recommended=TRUE (ver auditoría). Debe devolver FeatureCollection
    vacía, no un error — confirma que el filtro no rompe con resultado vacío.
    """
    resp = await client.get(
        "/api/v1/ml/recommendations",
        params={"income_stratum": 1},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "FeatureCollection"
    assert data["features"] == []


async def test_recommendations_expose_is_demo_flag(client: AsyncClient, admin_token: str):
    """Cada feature trae is_demo derivado de su model_version — permite al
    frontend distinguir zonas sembradas por seed_demo_data.py (model_version
    contiene 'demo') de inferencia real, sin duplicar la heurística."""
    resp = await client.get(
        "/api/v1/ml/recommendations",
        params={"limit": 500},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    for feature in data["features"]:
        props = feature["properties"]
        assert "is_demo" in props
        expected = bool(props["model_version"] and "demo" in props["model_version"].lower())
        assert props["is_demo"] == expected


async def test_model_metrics_expose_is_demo_flag(client: AsyncClient, admin_token: str, db_session: AsyncSession):
    """is_demo se deriva del version_name (contiene 'demo' -> True, si no -> False).
    Self-contained: no depende de qué versiones haya sembradas en el entorno."""
    versions = {
        "pytest_ml_v1.0-demo":  True,
        "pytest_ml_v2.1-prod":  False,
    }
    try:
        for version_name in versions:
            await db_session.execute(
                text(
                    "INSERT INTO model_versions "
                    "(version_name, training_date, metrics, features_used, is_active, created_at) "
                    "VALUES (:v, NOW(), '{}', '[]', FALSE, NOW())"
                ),
                {"v": version_name},
            )
        await db_session.commit()

        for version_name, expected_is_demo in versions.items():
            resp = await client.get(
                f"/api/v1/ml/models/{version_name}/metrics",
                headers=auth_headers(admin_token),
            )
            assert resp.status_code == 200
            assert resp.json()["is_demo"] is expected_is_demo
    finally:
        await db_session.execute(
            text("DELETE FROM model_versions WHERE version_name = ANY(:vs)"),
            {"vs": list(versions)},
        )
        await db_session.commit()


async def test_recommendations_combined_filters(client: AsyncClient, admin_token: str):
    """income_stratum combinado con priority y district_id: AND entre los tres,
    ningún feature devuelto debe violar ninguno de los tres filtros."""
    resp = await client.get(
        "/api/v1/ml/recommendations",
        params={
            "priority": "Alta",
            "district_id": 1,
            "income_stratum": [3, 4, 5],
            "limit": 500,
        },
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    for feature in data["features"]:
        props = feature["properties"]
        assert props["priority_label"] == "Alta"
        assert props["income_stratum"] in (3, 4, 5, None)
