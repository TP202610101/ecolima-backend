import io
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_role
from app.db.session import get_session
from app.models.user import User
from app.services.ml_service import (
    ModelNotAvailableError,
    _inference_tasks,
    _run_inference_bg,
    generate_synthetic_training_data,
    get_inference_status,
    get_training_set_data,
    get_training_set_stats,
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


@router.post("/run-inference")
async def run_inference_endpoint(
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
    # Verificar que hay un modelo disponible antes de encolar la tarea
    from app.services.ml_service import load_model
    try:
        load_model(payload.model_version)
    except ModelNotAvailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "MODEL_NOT_AVAILABLE",
                "message": str(exc),
            },
        )

    # Contar zonas inferibles para la estimación
    from sqlalchemy import and_, func, select
    from app.models.candidate_zone import CandidateZone
    from app.services.ml_service import FEATURE_COLUMNS
    not_null = [getattr(CandidateZone, col).isnot(None) for col in FEATURE_COLUMNS]
    estimated: int = (
        await db.execute(select(func.count()).select_from(CandidateZone).where(and_(*not_null)))
    ).scalar() or 0

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


# ── Recomendaciones (completado en P-15) ──────────────────────────────────────

@router.get("/recommendations")
async def ml_recommendations(
    _: User = Depends(get_current_user),
):
    """Placeholder — implementado en P-15."""
    return {"message": "ml recommendations — pendiente implementación en P-15"}
