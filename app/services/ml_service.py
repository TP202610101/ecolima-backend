import logging
import pickle
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import HTTPException, status
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.sql.expression import bindparam
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_zone import CandidateZone

logger = logging.getLogger(__name__)

# Local fallback path for the model
_LOCAL_MODEL_PATH = Path("models/lightgbm_model.pkl")

# ── Columnas del training set ─────────────────────────────────────────────────

# Orden exacto que espera LightGBM (CRÍTICO — no cambiar sin cambiar el modelo)
FEATURE_COLUMNS: list[str] = [
    "population_density",
    "income_stratum",
    "fuel_expenditure_sol",
    "edu_level_head",
    "household_size_avg",
    "pct_nse_ab",
    "pct_nse_de",
    "gpc_kg_per_capita_day",
    "pct_recyclable",
    "pct_plastic",
    "informal_recyclers_count",
    "road_density",
    "dist_to_main_road_m",
    "dist_to_market_m",
    "dist_to_nearest_point_m",
    "existing_points_500m",
    "has_park_300m",
    "slope_pct",
    "urbanized_area_pct",
    "recycling_potential_index",
]

# Columnas que van en el CSV de entrenamiento (IDs + features + target)
_TRAINING_COLS: list[str] = (
    ["zone_id", "centroid_lat", "centroid_lon"]
    + FEATURE_COLUMNS
    + ["is_suitable"]
)

# Columnas de Sección C — NUNCA deben aparecer en el training set
_SECTION_C: frozenset[str] = frozenset({
    "ml_score", "is_recommended", "priority_label",
    "recommendation_reason", "coverage_gap_m",
    "model_version", "inference_date", "geometry",
})

# Fallo rápido al cargar el módulo si alguien mete Sección C por error
assert not (_SECTION_C & set(_TRAINING_COLS)), (
    "CRITICAL: columna de Sección C detectada en _TRAINING_COLS — data leakage"
)


# ── Funciones de servicio ─────────────────────────────────────────────────────

async def get_training_set_data(db: AsyncSession) -> pd.DataFrame:
    """
    Devuelve candidate_zones etiquetadas (is_suitable IS NOT NULL) con solo
    las columnas permitidas — IDs, Sección B y target.
    Sección C y geometry quedan excluidas por construcción.
    """
    cols = [getattr(CandidateZone, c) for c in _TRAINING_COLS]
    rows = (
        await db.execute(
            select(*cols).where(CandidateZone.is_suitable.isnot(None))
        )
    ).mappings().all()

    df = pd.DataFrame([dict(r) for r in rows], columns=_TRAINING_COLS)
    if not df.empty and "has_park_300m" in df.columns:
        # LightGBM no acepta bool nativo — convertir a 0/1
        df["has_park_300m"] = df["has_park_300m"].astype(int)
    return df


async def get_training_set_stats(db: AsyncSession) -> dict:
    """
    Estadísticas del dataset de entrenamiento para diagnosticar desbalance
    de clases y disponibilidad de features antes del entrenamiento.
    """
    counts_row = (
        await db.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE is_suitable IS NOT NULL)  AS total_labeled,
                    COUNT(*) FILTER (WHERE is_suitable = 1)          AS positive_labels,
                    COUNT(*) FILTER (WHERE is_suitable = 0)          AS negative_labels,
                    COUNT(*) FILTER (WHERE is_suitable IS NULL)      AS null_labels,
                    COUNT(DISTINCT district_id)
                        FILTER (WHERE is_suitable IS NOT NULL)       AS districts_covered
                FROM candidate_zones
            """)
        )
    ).mappings().first()

    positive = int(counts_row["positive_labels"])
    negative = int(counts_row["negative_labels"])
    total_labeled = int(counts_row["total_labeled"])

    # Detectar qué features tienen al menos un valor no-NULL en las zonas etiquetadas
    features_available: list[str] = []
    if total_labeled > 0:
        availability_parts = ", ".join(
            f"MAX(CASE WHEN {col} IS NOT NULL THEN 1 ELSE 0 END) AS {col}"
            for col in FEATURE_COLUMNS
        )
        feat_row = (
            await db.execute(
                text(f"""
                    SELECT {availability_parts}
                    FROM candidate_zones
                    WHERE is_suitable IS NOT NULL
                """)
            )
        ).mappings().first()

        if feat_row:
            features_available = [
                col for col in FEATURE_COLUMNS if feat_row.get(col, 0) == 1
            ]

    return {
        "total_labeled":        total_labeled,
        "positive_labels":      positive,
        "negative_labels":      negative,
        "null_labels":          int(counts_row["null_labels"]),
        "class_imbalance_ratio": round(negative / positive, 2) if positive > 0 else None,
        "features_available":   features_available,
        "districts_covered":    int(counts_row["districts_covered"]),
    }


def generate_synthetic_training_data(n: int = 250) -> pd.DataFrame:
    """
    Genera n filas con distribuciones realistas para Lima Metropolitana
    usando numpy. Seed fijo (42) para reproducibilidad.
    Útil para verificar el pipeline ML sin datos reales completos.
    """
    rng = np.random.default_rng(seed=42)

    # Derivar variables base para calcular recycling_potential_index
    pop_density    = rng.normal(6000, 3000, n).clip(500, 18000)
    gpc            = rng.normal(0.85, 0.25, n).clip(0.1, 3.0)
    pct_recyclable = rng.normal(28, 8, n).clip(0, 100)

    df = pd.DataFrame({
        "zone_id":                  range(1, n + 1),
        "centroid_lat":             rng.uniform(-13.0, -11.5, n).round(6),
        "centroid_lon":             rng.uniform(-77.5, -76.5, n).round(6),
        # Sección B — distribuciones calibradas con datos INEI / MINAM Lima
        "population_density":       pop_density.round(2),
        "income_stratum":           rng.integers(1, 6, n),           # 1–5
        "fuel_expenditure_sol":     rng.normal(48, 18, n).clip(10, 120).round(2),
        "edu_level_head":           rng.normal(3.5, 1.5, n).clip(0, 7).round(2),
        "household_size_avg":       rng.normal(3.8, 1.0, n).clip(1, 15).round(2),
        "pct_nse_ab":               rng.normal(25, 20, n).clip(0, 100).round(2),
        "pct_nse_de":               rng.normal(30, 20, n).clip(0, 100).round(2),
        "gpc_kg_per_capita_day":    gpc.round(3),
        "pct_recyclable":           pct_recyclable.round(2),
        "pct_plastic":              rng.normal(22, 7, n).clip(0, 100).round(2),
        "informal_recyclers_count": rng.poisson(5, n).astype(int),
        "road_density":             rng.normal(12000, 5000, n).clip(0, 50000).round(1),
        "dist_to_main_road_m":      rng.normal(300, 200, n).clip(0, 5000).round(1),
        "dist_to_market_m":         rng.normal(600, 400, n).clip(0, 5000).round(1),
        "dist_to_nearest_point_m":  rng.normal(1500, 800, n).clip(0, 10000).round(1),
        "existing_points_500m":     rng.poisson(0.3, n).astype(int),
        "has_park_300m":            rng.binomial(1, 0.3, n).astype(int),  # 0/1
        "slope_pct":                rng.normal(8, 5, n).clip(0, 90).round(2),
        "urbanized_area_pct":       rng.normal(70, 20, n).clip(0, 100).round(2),
        # Derivada: pop × gpc × pct_recyclable / 100
        "recycling_potential_index": (pop_density * gpc * pct_recyclable / 100).round(2),
        # Target — ~5% positivos, refleja desbalance real del dataset Lima
        "is_suitable":              rng.binomial(1, 0.05, n).astype(int),
    })

    return df[_TRAINING_COLS]


# ── Inferencia LightGBM ───────────────────────────────────────────────────────

class ModelNotAvailableError(Exception):
    """Raised when no LightGBM model is found in Azure Blob or locally."""


# In-memory model cache — evita recargar el .pkl en cada request
_model_cache: dict[str, Any] = {}

# In-memory task registry — estado de tareas de inferencia en curso
_inference_tasks: dict[str, dict] = {}


def _try_load_from_azure(version: str) -> Any | None:
    """Intenta cargar el modelo desde Azure Blob Storage. Retorna None si falla."""
    try:
        from app.core.config import Settings
        settings = Settings()
        conn_str = settings.azure_blob_connection_string
        # Evitar llamada real si el .env aún tiene el placeholder
        if not conn_str or "AccountName=..." in conn_str:
            return None
        from azure.storage.blob import BlobServiceClient
        client = BlobServiceClient.from_connection_string(conn_str)
        container = client.get_container_client(settings.azure_blob_container_models)
        blob_name = "lightgbm_model.pkl" if version == "latest" else f"lightgbm_model_{version}.pkl"
        data = container.download_blob(blob_name).readall()
        model = pickle.loads(data)
        logger.info("Modelo '%s' cargado desde Azure Blob.", version)
        return model
    except Exception as exc:
        logger.debug("Azure Blob no disponible (%s) — usando fallback local.", exc)
        return None


def _try_load_from_local() -> Any | None:
    """Intenta cargar el modelo desde models/lightgbm_model.pkl. Retorna None si no existe."""
    if not _LOCAL_MODEL_PATH.exists():
        return None
    with open(_LOCAL_MODEL_PATH, "rb") as fh:
        model = pickle.load(fh)
    logger.info("Modelo cargado desde %s (fallback local).", _LOCAL_MODEL_PATH)
    return model


def load_model(version: str = "latest") -> Any:
    """
    Carga el modelo LightGBM en el siguiente orden de prioridad:
      1. Caché en memoria (sin I/O).
      2. Azure Blob Storage (conexión configurada en .env).
      3. models/lightgbm_model.pkl (fallback local).
    Lanza ModelNotAvailableError si ninguna fuente tiene el modelo.
    """
    if version in _model_cache:
        return _model_cache[version]

    model = _try_load_from_azure(version) or _try_load_from_local()
    if model is None:
        raise ModelNotAvailableError(
            "No hay modelo LightGBM disponible. "
            "Opciones: (1) coloca el archivo en models/lightgbm_model.pkl, "
            "(2) sube el modelo a Azure Blob y configura AZURE_BLOB_CONNECTION_STRING."
        )
    _model_cache[version] = model
    return model


def get_priority_label(score: float, high: float = 0.7, medium: float = 0.4) -> str:
    if score >= high:
        return "Alta"
    if score >= medium:
        return "Media"
    return "Baja"


def _generate_basic_reason(
    shap_row: np.ndarray,
    feature_names: list[str],
    feature_values: dict,  # reservado para P-15 (labels + unidades)
) -> str:
    """
    Genera texto explicativo básico a partir de los SHAP values de una celda.
    P-15 reemplaza esto con la versión completa usando FEATURE_LABELS y unidades.
    """
    top_idx = sorted(range(len(shap_row)), key=lambda i: abs(float(shap_row[i])), reverse=True)[:3]
    parts = []
    for i in top_idx:
        direction = "alta" if float(shap_row[i]) > 0 else "baja"
        parts.append(f"{direction} {feature_names[i]}")
    return ("Zona recomendada por: " + ", ".join(parts) + ".") if parts else "Sin explicación disponible."


async def _get_inference_zones(db: AsyncSession) -> tuple[list[int], pd.DataFrame]:
    """Devuelve zonas donde TODAS las features de Sección B son NOT NULL."""
    not_null = [getattr(CandidateZone, col).isnot(None) for col in FEATURE_COLUMNS]
    cols = [CandidateZone.zone_id, *[getattr(CandidateZone, c) for c in FEATURE_COLUMNS]]
    rows = (
        await db.execute(select(*cols).where(and_(*not_null)))
    ).mappings().all()

    if not rows:
        return [], pd.DataFrame(columns=["zone_id"] + FEATURE_COLUMNS)

    df = pd.DataFrame([dict(r) for r in rows])
    zone_ids = df["zone_id"].tolist()
    return zone_ids, df


_UPDATE_SECTION_C = (
    update(CandidateZone)
    .where(CandidateZone.zone_id == bindparam("b_zone_id"))
    .values(
        ml_score=bindparam("b_ml_score"),
        is_recommended=bindparam("b_is_recommended"),
        priority_label=bindparam("b_priority_label"),
        recommendation_reason=bindparam("b_recommendation_reason"),
        model_version=bindparam("b_model_version"),
        inference_date=bindparam("b_inference_date"),
    )
)


async def _bulk_update_section_c(db: AsyncSession, params: list[dict]) -> None:
    """Actualiza Sección C en lotes de 500 filas usando executemany."""
    _BATCH = 500
    for i in range(0, len(params), _BATCH):
        await db.execute(_UPDATE_SECTION_C, params[i : i + _BATCH])
    await db.commit()


async def run_inference(
    db: AsyncSession,
    model_version: str = "latest",
    threshold: float = 0.5,
    task_id: str | None = None,
) -> dict:
    """
    Pipeline completo de inferencia:
      1. Carga el modelo (caché → Azure → local).
      2. Obtiene candidate_zones con todas las features completas.
      3. predict_proba → ml_score.
      4. SHAP → recommendation_reason.
      5. UPDATE masivo de Sección C (ml_score, is_recommended, priority_label,
         recommendation_reason, model_version, inference_date).
    Retorna stats de la ejecución.
    """
    def _update_task(pct: int, zones: int = 0) -> None:
        if task_id and task_id in _inference_tasks:
            _inference_tasks[task_id].update({"progress_pct": pct, "zones_processed": zones})

    # 1. Cargar modelo
    _update_task(5)
    model = load_model(model_version)
    _update_task(15)

    # 2. Obtener zonas inferibles
    zone_ids, df = await _get_inference_zones(db)
    n = len(zone_ids)
    if n == 0:
        logger.warning("run_inference: ninguna zona tiene todas las features completas.")
        return {"zones_processed": 0, "high_priority": 0, "medium_priority": 0, "low_priority": 0}
    _update_task(25, n)

    # 3. Preparar features en el orden exacto que espera LightGBM
    X = df[FEATURE_COLUMNS].copy()
    X["has_park_300m"] = X["has_park_300m"].astype(int)

    # 4. Inferencia
    scores: np.ndarray = model.predict_proba(X.values)[:, 1]
    _update_task(55, n)

    # Leer umbrales desde settings si están disponibles
    try:
        from app.core.config import Settings
        s = Settings()
        high_thr, med_thr = s.ml_priority_high, s.ml_priority_medium
    except Exception:
        high_thr, med_thr = 0.7, 0.4

    labels = [get_priority_label(float(sc), high_thr, med_thr) for sc in scores]
    is_recs = [bool(float(sc) > threshold) for sc in scores]

    # 5. SHAP → recommendation_reason
    try:
        import shap  # lazy import — pesado, solo al correr inferencia
        explainer = shap.TreeExplainer(model)
        shap_vals = explainer.shap_values(X)
        if isinstance(shap_vals, list):
            shap_vals = shap_vals[1]  # clase positiva
        reasons = [
            _generate_basic_reason(shap_vals[i], FEATURE_COLUMNS, X.iloc[i].to_dict())
            for i in range(n)
        ]
    except Exception as exc:
        logger.warning("SHAP no disponible (%s) — usando razón genérica.", exc)
        reasons = ["Sin explicación disponible."] * n
    _update_task(80, n)

    # 6. Actualizar Sección C
    now = datetime.utcnow()
    mv_str = model_version if model_version != "latest" else "v-unknown"
    params = [
        {
            "b_zone_id":               int(zone_ids[i]),
            "b_ml_score":              float(scores[i]),
            "b_is_recommended":        is_recs[i],
            "b_priority_label":        labels[i],
            "b_recommendation_reason": reasons[i],
            "b_model_version":         mv_str,
            "b_inference_date":        now,
        }
        for i in range(n)
    ]
    await _bulk_update_section_c(db, params)
    _update_task(100, n)

    return {
        "zones_processed": n,
        "high_priority":   labels.count("Alta"),
        "medium_priority": labels.count("Media"),
        "low_priority":    labels.count("Baja"),
    }


async def _run_inference_bg(task_id: str, model_version: str, threshold: float) -> None:
    """Background coroutine — crea su propia sesión BD independiente del request."""
    from app.db.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            result = await run_inference(db, model_version=model_version, threshold=threshold, task_id=task_id)
        _inference_tasks[task_id] = {"status": "done", "progress_pct": 100, **result}
    except ModelNotAvailableError as exc:
        _inference_tasks[task_id] = {
            "status": "error", "progress_pct": 0, "zones_processed": 0,
            "error": str(exc),
        }
    except Exception as exc:
        logger.exception("Inferencia fallida (task=%s)", task_id)
        _inference_tasks[task_id] = {
            "status": "error", "progress_pct": 0, "zones_processed": 0,
            "error": str(exc),
        }


def get_inference_status(task_id: str) -> dict:
    if task_id not in _inference_tasks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TASK_NOT_FOUND", "message": f"No existe tarea con id '{task_id}'."},
        )
    return _inference_tasks[task_id]
