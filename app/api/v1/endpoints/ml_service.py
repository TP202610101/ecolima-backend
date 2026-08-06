"""Endpoints proxy hacia la API de ecolima-ml (contrato v0.3).

Estos endpoints exponen el servicio ML externo (repo ecolima-ml) a través del
backend, de modo que ecolima-frontend nunca hable directo con ecolima-ml.

Ruta base: /api/v1/ml/service

ADVERTENCIA METODOLÓGICA: el modelo upstream está entrenado con datos
simulados (v0.3). Estas rutas validan el contrato técnico de integración;
sus salidas no son recomendaciones finales de la tesis.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.dependencies import get_current_user, require_role
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.ml import (
    MLBatchPredictionRequest,
    MLRecommendationRequest,
    MLZonePredictionRequest,
)
from app.services.ml_api_client import (
    MLApiClient,
    MLApiError,
    MLApiUnavailableError,
    get_ml_api_client,
)

router = APIRouter()


def _map_ml_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MLApiUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "ML_API_UNAVAILABLE", "message": str(exc)},
        )
    if isinstance(exc, MLApiError):
        # exc.upstream_detail (texto crudo del upstream, ya logueado en
        # ml_api_client._request) NUNCA se expone al cliente -- ver
        # auditoria-seguridad-backend.md I8.
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "ML_API_ERROR",
                "message": f"La API de ecolima-ml respondió con un error ({exc.status_code}).",
            },
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "ML_PROXY_ERROR", "message": str(exc)},
    )


@router.get("/health")
async def ml_service_health(
    model_name: str | None = None,
    _: User = Depends(get_current_user),
    client: MLApiClient = Depends(get_ml_api_client),
):
    """Auth (todos) — estado del servicio ML externo y del modelo cargado."""
    try:
        return await client.health(model_name=model_name)
    except (MLApiUnavailableError, MLApiError) as exc:
        raise _map_ml_error(exc)


@router.get("/metadata")
async def ml_service_metadata(
    model_name: str | None = None,
    _: User = Depends(get_current_user),
    client: MLApiClient = Depends(get_ml_api_client),
):
    """Auth (todos) — metadata y umbral del modelo servido por ecolima-ml."""
    try:
        return await client.model_metadata(model_name=model_name)
    except (MLApiUnavailableError, MLApiError) as exc:
        raise _map_ml_error(exc)


@router.post("/predict")
@limiter.limit("30/minute")
async def ml_service_predict(
    request: Request,
    payload: MLZonePredictionRequest,
    _: User = Depends(require_role("admin")),
    client: MLApiClient = Depends(get_ml_api_client),
):
    """admin — predicción para una zona candidata (opcionalmente con explicación SHAP)."""
    try:
        return await client.predict(
            zone=payload.zone.model_dump(exclude_none=True),
            zone_id=payload.zone_id,
            model_name=payload.model_name,
            include_explanation=payload.include_explanation,
        )
    except (MLApiUnavailableError, MLApiError) as exc:
        raise _map_ml_error(exc)


@router.post("/predict/batch")
@limiter.limit("10/minute")
async def ml_service_predict_batch(
    request: Request,
    payload: MLBatchPredictionRequest,
    _: User = Depends(require_role("admin")),
    client: MLApiClient = Depends(get_ml_api_client),
):
    """admin — predicción batch para zonas candidatas."""
    try:
        return await client.predict_batch(
            zones=[z.model_dump(exclude_none=True) for z in payload.zones],
            zone_ids=payload.zone_ids,
            model_name=payload.model_name,
        )
    except (MLApiUnavailableError, MLApiError) as exc:
        raise _map_ml_error(exc)


@router.post("/recommendations")
@limiter.limit("10/minute")
async def ml_service_recommendations(
    request: Request,
    payload: MLRecommendationRequest,
    _: User = Depends(require_role("admin")),
    client: MLApiClient = Depends(get_ml_api_client),
):
    """admin — ranking top-N de zonas candidatas según el modelo externo."""
    try:
        return await client.recommendations(
            zones=[z.model_dump(exclude_none=True) for z in payload.zones],
            zone_ids=payload.zone_ids,
            model_name=payload.model_name,
            top_n=payload.top_n,
        )
    except (MLApiUnavailableError, MLApiError) as exc:
        raise _map_ml_error(exc)
