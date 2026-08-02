"""Tests del proxy /api/v1/ml/service/* hacia la API de ecolima-ml.

El cliente ML se mockea via dependency_overrides: no requiere que la API de
ecolima-ml esté corriendo. Valida contrato, auth y mapeo de errores (503/502).
"""

import pytest
from httpx import AsyncClient

from main import app
from app.services.ml_api_client import (
    MLApiError,
    MLApiUnavailableError,
    get_ml_api_client,
)
from tests.conftest import auth_headers


class FakeMLApiClient:
    """Respuestas fijas con la forma del contrato v0.3 de ecolima-ml."""

    async def health(self, model_name=None):
        return {
            "status": "ok",
            "model_name": model_name or "lgbm_recycling_simulated_v0_3",
            "model_loaded": True,
            "threshold": 0.74,
            "target": "is_suitable_simulated",
        }

    async def model_metadata(self, model_name=None):
        return {
            "model_name": model_name or "lgbm_recycling_simulated_v0_3",
            "threshold": 0.74,
            "metadata": {"target": "is_suitable_simulated"},
        }

    async def predict(self, zone, zone_id=None, model_name=None, include_explanation=False):
        result = {"zone_id": zone_id or 0, "score": 0.81, "is_suitable": True}
        if include_explanation:
            result["explanation"] = {"top_features": []}
        return result

    async def predict_batch(self, zones, zone_ids=None, model_name=None):
        ids = zone_ids or list(range(len(zones)))
        return {"items": [{"zone_id": i, "score": 0.5, "is_suitable": False} for i in ids]}

    async def recommendations(self, zones, zone_ids=None, model_name=None, top_n=10):
        ids = zone_ids or list(range(len(zones)))
        items = [{"zone_id": i, "score": 0.9, "rank": n + 1} for n, i in enumerate(ids[:top_n])]
        return {"items": items}


class UnavailableMLApiClient:
    async def health(self, model_name=None):
        raise MLApiUnavailableError("connection refused")


class UpstreamErrorMLApiClient:
    async def predict(self, zone, zone_id=None, model_name=None, include_explanation=False):
        raise MLApiError(400, "columna faltante: poblacion_500m")


@pytest.fixture
def fake_ml_client():
    app.dependency_overrides[get_ml_api_client] = lambda: FakeMLApiClient()
    yield
    app.dependency_overrides.pop(get_ml_api_client, None)


ZONE = {"candidate_id": 7, "poblacion_500m": 1200, "distancia_punto_m": 850}


@pytest.mark.asyncio
async def test_ml_service_health_ok(client: AsyncClient, admin_token: str, fake_ml_client):
    resp = await client.get("/api/v1/ml/service/health", headers=auth_headers(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True


@pytest.mark.asyncio
async def test_ml_service_health_requires_auth(client: AsyncClient, fake_ml_client):
    resp = await client.get("/api/v1/ml/service/health")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_ml_service_metadata_ok(client: AsyncClient, admin_token: str, fake_ml_client):
    resp = await client.get("/api/v1/ml/service/metadata", headers=auth_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json()["threshold"] == 0.74


@pytest.mark.asyncio
async def test_ml_service_predict_ok(client: AsyncClient, admin_token: str, fake_ml_client):
    resp = await client.post(
        "/api/v1/ml/service/predict",
        json={"zone": ZONE, "zone_id": 7},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["zone_id"] == 7


@pytest.mark.asyncio
async def test_ml_service_predict_batch_ok(client: AsyncClient, admin_token: str, fake_ml_client):
    resp = await client.post(
        "/api/v1/ml/service/predict/batch",
        json={"zones": [ZONE, ZONE], "zone_ids": [1, 2]},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


@pytest.mark.asyncio
async def test_ml_service_recommendations_ok(client: AsyncClient, admin_token: str, fake_ml_client):
    resp = await client.post(
        "/api/v1/ml/service/recommendations",
        json={"zones": [ZONE, ZONE, ZONE], "top_n": 2},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


@pytest.mark.asyncio
async def test_ml_service_batch_empty_zones_422(client: AsyncClient, admin_token: str, fake_ml_client):
    resp = await client.post(
        "/api/v1/ml/service/predict/batch",
        json={"zones": []},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ml_service_unavailable_maps_503(client: AsyncClient, admin_token: str):
    app.dependency_overrides[get_ml_api_client] = lambda: UnavailableMLApiClient()
    try:
        resp = await client.get("/api/v1/ml/service/health", headers=auth_headers(admin_token))
    finally:
        app.dependency_overrides.pop(get_ml_api_client, None)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "ML_API_UNAVAILABLE"


@pytest.mark.asyncio
async def test_ml_service_upstream_error_maps_502(client: AsyncClient, admin_token: str):
    app.dependency_overrides[get_ml_api_client] = lambda: UpstreamErrorMLApiClient()
    try:
        resp = await client.post(
            "/api/v1/ml/service/predict",
            json={"zone": ZONE},
            headers=auth_headers(admin_token),
        )
    finally:
        app.dependency_overrides.pop(get_ml_api_client, None)
    assert resp.status_code == 502
    body = resp.json()
    assert body["detail"]["code"] == "ML_API_ERROR"
    # I8: el texto crudo del upstream (potencialmente un traceback) no debe
    # reenviarse al cliente -- solo se loguea server-side.
    assert "poblacion_500m" not in body["detail"]["message"]


@pytest.mark.asyncio
async def test_ml_api_client_unavailable_error_does_not_leak_base_url():
    """I7: si la API ML no responde, el mensaje de MLApiUnavailableError no
    debe incluir la URL/IP interna del servicio -- eso es justo lo que
    _map_ml_error() reenvía tal cual al cliente vía str(exc)."""
    from app.services.ml_api_client import MLApiClient

    unreachable_url = "http://127.0.0.1:1"
    api_client = MLApiClient(base_url=unreachable_url, timeout_seconds=1.0)
    with pytest.raises(MLApiUnavailableError) as exc_info:
        await api_client.health()

    message = str(exc_info.value)
    assert "127.0.0.1" not in message
    assert unreachable_url not in message
