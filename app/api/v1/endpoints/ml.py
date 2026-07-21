import asyncio
import io
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_role
from app.core.limiter import limiter
from app.db.session import get_session
from app.models.user import User
from app.services.ml_service import (
    ModelNotAvailableError,
    _inference_tasks,
    _run_inference_bg,
    activate_model,
    count_inferable_zones,
    generate_synthetic_training_data,
    get_all_models,
    get_inference_status,
    get_model_metrics,
    get_recommendations_geojson,
    get_training_set_data,
    get_training_set_stats,
    register_model,
)

router = APIRouter()


# ── Training set ──────────────────────────────────────────────────────────────

@router.get("/training-set/export")
async def training_set_export(
    _: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """
    admin — exporta candidate_zones etiquetadas como CSV para LightGBM.
    Incluye: zone_id, centroid_lat, centroid_lon, Sección B (20 features), is_suitable.
    NUNCA incluye: Sección C (ml_score, priority_label, …) ni geometry.
    """
    df = await get_training_set_data(db)
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"grid_lima_train_{date_str}.csv"
    csv_bytes = df.to_csv(index=False, encoding="utf-8").encode("utf-8")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/training-set/stats")
async def training_set_stats(
    _: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """admin — estadísticas del dataset de entrenamiento."""
    return await get_training_set_stats(db)


@router.post("/training-set/simulate")
async def training_set_simulate(
    n: int = 250,
    _: User = Depends(require_role("admin")),
):
    """admin — genera n filas sintéticas con distribuciones realistas para Lima (max 1000)."""
    df = generate_synthetic_training_data(n=min(n, 1000))
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"grid_lima_synthetic_{date_str}.csv"
    csv_bytes = df.to_csv(index=False, encoding="utf-8").encode("utf-8")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Inferencia ────────────────────────────────────────────────────────────────

class RunInferenceRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_version: str = "latest"
    threshold: float = 0.5


@router.post("/run-inference", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("20/minute")
async def run_inference_endpoint(
    request: Request,
    payload: RunInferenceRequest,
    background_tasks: BackgroundTasks,
    _: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """
    admin — lanza la inferencia LightGBM como BackgroundTask.
    Retorna inmediatamente con task_id. Consultar estado en GET /inference-status/{task_id}.
    Requiere que exista un modelo cargable (Azure Blob o models/lightgbm_model.pkl).
    """
    from app.services.ml_service import load_model
    try:
        await asyncio.to_thread(load_model, payload.model_version)
    except ModelNotAvailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "MODEL_NOT_AVAILABLE",
                "message": str(exc),
            },
        )

    estimated: int = await count_inferable_zones(db)

    task_id = str(uuid.uuid4())
    _inference_tasks[task_id] = {
        "status": "running",
        "progress_pct": 0,
        "zones_processed": 0,
    }
    background_tasks.add_task(
        _run_inference_bg,
        task_id,
        payload.model_version,
        payload.threshold,
    )
    return {
        "task_id":         task_id,
        "status":          "running",
        "estimated_zones": estimated,
    }


@router.get("/inference-status/{task_id}")
async def inference_status(
    task_id: str,
    _: User = Depends(require_role("admin")),
):
    """admin — consulta el estado de una tarea de inferencia."""
    return get_inference_status(task_id)


# ── Recomendaciones ───────────────────────────────────────────────────────────

@router.get("/recommendations")
@limiter.limit("20/minute")
async def ml_recommendations(
    request: Request,
    priority: str = "all",
    district_id: int | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """
    Auth (todos) — GeoJSON FeatureCollection de zonas recomendadas (is_recommended=true).
    Filtros: priority=Alta|Media|Baja|all, district_id, limit (max 500).
    ml_score solo visible para rol admin — analista y ciudadano no lo reciben.
    Ordenado por ml_score DESC.
    """
    valid_priorities = {"Alta", "Media", "Baja", "all"}
    if priority not in valid_priorities:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"priority debe ser uno de: {sorted(valid_priorities)}",
        )

    data = await get_recommendations_geojson(
        db,
        priority=priority if priority != "all" else None,
        district_id=district_id,
        limit=min(limit, 500),
        include_ml_score=current_user.role == "admin",
    )
    return JSONResponse(content=data, media_type="application/geo+json")


# ── Versionado de modelos ─────────────────────────────────────────────────────


class RegisterModelRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    version_name: str
    artifact_url: str | None = None
    metrics: dict | None = None
    features_used: list[str] | None = None


class ModelActivationRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    is_active: bool


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def register_model_endpoint(
    payload: RegisterModelRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """
    admin — registra una nueva versión de modelo.
    Llamado por el script de entrenamiento de Nikole tras guardar el .pkl en Azure Blob.
    """
    return await register_model(
        db,
        version_name=payload.version_name,
        artifact_url=payload.artifact_url,
        metrics=payload.metrics,
        features_used=payload.features_used,
        trained_by=current_user.user_id,
    )


@router.get("/models/{version}/metrics")
async def model_metrics(
    version: str,
    _: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """admin — métricas detalladas + comparativa vs versión anterior."""
    return await get_model_metrics(db, version_name=version)


@router.patch("/models/{version}")
async def update_model_endpoint(
    version: str,
    payload: ModelActivationRequest,
    _: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """admin — activa esta versión (is_active=true) y desactiva las demás. Limpia el caché en memoria."""
    if not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "UNSUPPORTED_TRANSITION",
                "message": "Solo se soporta is_active=true",
                "allowed": [True],
            },
        )
    return await activate_model(db, version_name=version)


@router.get("/models")
async def list_models(
    _: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """admin — lista todas las versiones registradas, más recientes primero."""
    return await get_all_models(db)
