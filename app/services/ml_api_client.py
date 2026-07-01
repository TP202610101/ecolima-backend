"""Cliente HTTP hacia la API de ecolima-ml (contrato v0.3).

ecolima-backend consume el servicio ML por HTTP (decisión de arquitectura
2026-07-01): los repos quedan desacoplados y cada uno se despliega por
separado. La URL se configura con ML_API_URL (default http://localhost:8001).

Endpoints upstream (ecolima-ml, src/ml/api/app.py):
    GET  /health
    GET  /model/metadata
    POST /predict
    POST /predict/batch
    POST /recommendations

Nota metodológica: el modelo servido por ecolima-ml es de desarrollo técnico
(entrenado con datos simulados v0.3). Sus salidas NO son recomendaciones
finales de la tesis y no deben presentarse como evidencia empírica.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings

settings = Settings()


class MLApiUnavailableError(Exception):
    """La API de ecolima-ml no responde (conexión rechazada / timeout)."""


class MLApiError(Exception):
    """La API de ecolima-ml respondió con un error HTTP."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"ML API {status_code}: {detail}")


class MLApiClient:
    """Cliente async fino sobre la API de ecolima-ml."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ):
        self.base_url = (base_url or settings.ml_api_url).rstrip("/")
        self.timeout = timeout_seconds or settings.ml_api_timeout_seconds

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout
            ) as client:
                response = await client.request(method, path, params=params, json=json)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise MLApiUnavailableError(
                f"No se pudo contactar la API ML en {self.base_url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise MLApiError(response.status_code, str(detail))

        return response.json()

    async def health(self, model_name: str | None = None) -> dict[str, Any]:
        params = {"model_name": model_name} if model_name else None
        return await self._request("GET", "/health", params=params)

    async def model_metadata(self, model_name: str | None = None) -> dict[str, Any]:
        params = {"model_name": model_name} if model_name else None
        return await self._request("GET", "/model/metadata", params=params)

    async def predict(
        self,
        zone: dict[str, Any],
        zone_id: str | int | None = None,
        model_name: str | None = None,
        include_explanation: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "zone": zone,
            "include_explanation": include_explanation,
        }
        if zone_id is not None:
            payload["zone_id"] = zone_id
        if model_name:
            payload["model_name"] = model_name
        return await self._request("POST", "/predict", json=payload)

    async def predict_batch(
        self,
        zones: list[dict[str, Any]],
        zone_ids: list[str | int] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"zones": zones}
        if zone_ids is not None:
            payload["zone_ids"] = zone_ids
        if model_name:
            payload["model_name"] = model_name
        return await self._request("POST", "/predict/batch", json=payload)

    async def recommendations(
        self,
        zones: list[dict[str, Any]],
        zone_ids: list[str | int] | None = None,
        model_name: str | None = None,
        top_n: int = 10,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"zones": zones, "top_n": top_n}
        if zone_ids is not None:
            payload["zone_ids"] = zone_ids
        if model_name:
            payload["model_name"] = model_name
        return await self._request("POST", "/recommendations", json=payload)


_client: MLApiClient | None = None


def get_ml_api_client() -> MLApiClient:
    """Dependencia FastAPI — instancia perezosa reutilizable."""
    global _client
    if _client is None:
        _client = MLApiClient()
    return _client
