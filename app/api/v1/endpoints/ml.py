import io
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_role
from app.db.session import get_session
from app.models.user import User
from app.services.ml_service import (
    generate_synthetic_training_data,
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
    """
    admin — estadísticas del dataset de entrenamiento:
    tamaño, desbalance de clases, features disponibles, distritos cubiertos.
    """
    return await get_training_set_stats(db)


@router.post("/training-set/simulate")
async def training_set_simulate(
    n: int = 250,
    _: User = Depends(require_role("admin")),
):
    """
    admin — genera n filas sintéticas con distribuciones realistas para Lima.
    Útil para verificar el pipeline ML cuando candidate_zones aún no tiene
    features completas. Seed fijo (42) para reproducibilidad.
    max n = 1000.
    """
    df = generate_synthetic_training_data(n=min(n, 1000))
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"grid_lima_synthetic_{date_str}.csv"
    csv_bytes = df.to_csv(index=False, encoding="utf-8").encode("utf-8")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Recomendaciones (placeholder hasta P-14/P-15) ─────────────────────────────

@router.get("/recommendations")
async def ml_recommendations(
    _: User = Depends(get_current_user),
):
    """Placeholder — implementado en P-14/P-15."""
    return {"message": "ml recommendations — pendiente implementación en P-14"}
