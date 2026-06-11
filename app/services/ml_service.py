import json
import logging
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import HTTPException, status
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_zone import CandidateZone
from app.models.model_version import ModelVersion

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


# ── SHAP — mapas de etiquetas y unidades para el analista municipal ───────────

FEATURE_LABELS: dict[str, str] = {
    "population_density":        "densidad poblacional",
    "income_stratum":            "estrato de ingreso del distrito",
    "fuel_expenditure_sol":      "gasto promedio en combustible",
    "edu_level_head":            "nivel educativo del hogar",
    "household_size_avg":        "tamaño promedio del hogar",
    "pct_nse_ab":                "% hogares NSE A/B",
    "pct_nse_de":                "% hogares NSE D/E",
    "gpc_kg_per_capita_day":     "generación de residuos per cápita",
    "pct_recyclable":            "% de residuos reciclables",
    "pct_plastic":               "% de plástico en residuos",
    "informal_recyclers_count":  "recicladores formalizados en el distrito",
    "road_density":              "densidad de la red vial",
    "dist_to_main_road_m":       "distancia a la vía principal",
    "dist_to_market_m":          "distancia al mercado más cercano",
    "dist_to_nearest_point_m":   "distancia al punto de reciclaje más cercano",
    "existing_points_500m":      "puntos de reciclaje existentes en 500m",
    "has_park_300m":             "presencia de parque a menos de 300m",
    "slope_pct":                 "pendiente del terreno",
    "urbanized_area_pct":        "% de área urbanizada",
    "recycling_potential_index": "índice de potencial reciclable",
}

FEATURE_UNITS: dict[str, str] = {
    "population_density":        "hab/km²",
    "fuel_expenditure_sol":      "S/./mes",
    "gpc_kg_per_capita_day":     "kg/hab/día",
    "dist_to_main_road_m":       "m",
    "dist_to_market_m":          "m",
    "dist_to_nearest_point_m":   "m",
    "slope_pct":                 "%",
    "urbanized_area_pct":        "%",
    "pct_nse_ab":                "%",
    "pct_nse_de":                "%",
    "pct_recyclable":            "%",
    "pct_plastic":               "%",
    "recycling_potential_index": "",
}

_DIST_FEATURES = frozenset({"dist_to_nearest_point_m", "dist_to_main_road_m", "dist_to_market_m"})


def _format_feature_value(feature: str, value: Any) -> str:
    """Convierte un valor numérico de feature al formato legible para el analista."""
    if value is None:
        return "N/D"
    unit = FEATURE_UNITS.get(feature, "")
    if feature in _DIST_FEATURES:
        v = float(value)
        return f"{v / 1000:.1f} km" if v >= 1000 else f"{v:.0f} m"
    if unit == "%":
        return f"{float(value):.1f}%"
    if feature == "population_density":
        return f"{float(value):,.0f} hab/km²"
    if feature == "gpc_kg_per_capita_day":
        return f"{float(value):.2f} kg/hab/día"
    if feature == "recycling_potential_index":
        return f"índice {float(value):.0f}"
    if feature == "has_park_300m":
        return "sí" if int(value) else "no"
    if feature == "income_stratum":
        return f"estrato {int(value)}"
    if isinstance(value, (int, float)):
        return f"{float(value):.1f} {unit}".strip()
    return str(value)


def generate_explanation(
    shap_values: np.ndarray,
    feature_values: dict,
    top_n: int = 3,
) -> str:
    """
    Genera texto explicativo legible para el analista municipal a partir de
    los SHAP values de una celda. Toma las top_n features por |shap_value|
    y las convierte en una frase en español con etiquetas y unidades reales.

    Ejemplo de output:
        "Zona recomendada por: alta densidad poblacional (8,540 hab/km²),
         baja distancia al punto de reciclaje más cercano (450 m) y
         alto índice de potencial reciclable (índice 312)."
    """
    indexed = sorted(
        enumerate(shap_values),
        key=lambda x: abs(float(x[1])),
        reverse=True,
    )[:top_n]

    parts: list[str] = []
    for idx, shap_val in indexed:
        feature = FEATURE_COLUMNS[idx]
        label = FEATURE_LABELS.get(feature, feature)
        val_str = _format_feature_value(feature, feature_values.get(feature))
        direction = "alta" if float(shap_val) > 0 else "baja"
        parts.append(f"{direction} {label} ({val_str})")

    if not parts:
        return "Sin explicación disponible."
    if len(parts) == 1:
        reasons_str = parts[0]
    elif len(parts) == 2:
        reasons_str = f"{parts[0]} y {parts[1]}"
    else:
        reasons_str = f"{', '.join(parts[:-1])} y {parts[-1]}"

    return f"Zona recomendada por: {reasons_str}."


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


_UPDATE_SECTION_C_SQL = text("""
    UPDATE candidate_zones
    SET
        ml_score              = :b_ml_score,
        is_recommended        = :b_is_recommended,
        priority_label        = :b_priority_label,
        recommendation_reason = :b_recommendation_reason,
        model_version         = :b_model_version,
        inference_date        = :b_inference_date
    WHERE zone_id = :b_zone_id
""")


async def _bulk_update_section_c(db: AsyncSession, params: list[dict]) -> None:
    """
    Actualiza Sección C fila a fila usando text() — bypasea el ORM session
    bulk machinery que requiere PK explícita en el dict. Para las ~11,000 zonas
    del grid completo el tiempo total sigue siendo aceptable en BackgroundTask.
    """
    for p in params:
        await db.execute(_UPDATE_SECTION_C_SQL, p)
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
    # lgb.Booster (API nativa)  → .predict()       retorna probs directamente
    # LGBMClassifier (sklearn)  → .predict_proba() retorna array (n, 2)
    if hasattr(model, "predict_proba"):
        scores: np.ndarray = model.predict_proba(X.values)[:, 1]
    else:
        scores = model.predict(X.values)
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
            generate_explanation(shap_vals[i], X.iloc[i].to_dict())
            for i in range(n)
        ]
    except Exception as exc:
        logger.warning("SHAP no disponible (%s) — usando razón genérica.", exc)
        reasons = ["Sin explicación disponible."] * n
    _update_task(80, n)

    # 6. Resolver version_name desde model_versions (is_active=TRUE) o fallback
    active_row = (
        await db.execute(
            select(ModelVersion.version_name).where(ModelVersion.is_active.is_(True))
        )
    ).scalar_one_or_none()

    if active_row:
        mv_str = active_row
    elif model_version != "latest":
        mv_str = model_version
    else:
        mv_str = "v-unknown"

    # 7. Actualizar Sección C
    now = datetime.utcnow()
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


# ── Recomendaciones GeoJSON ───────────────────────────────────────────────────

async def get_recommendations_geojson(
    db: AsyncSession,
    priority: str | None = None,
    district_id: int | None = None,
    limit: int = 50,
    include_ml_score: bool = False,
) -> dict:
    """
    Devuelve candidate_zones recomendadas (is_recommended=true) como GeoJSON.
    ml_score se incluye solo cuando include_ml_score=True (rol admin).
    Ordenadas por ml_score DESC para priorizar las mejores zonas primero.
    """
    # Construir WHERE dinámicamente para evitar AmbiguousParameterError en asyncpg:
    # asyncpg no puede inferir el tipo de un parámetro $N cuando su valor es None.
    conditions = ["cz.is_recommended = TRUE"]
    params: dict = {"limit": limit}
    if priority is not None:
        conditions.append("cz.priority_label = :priority")
        params["priority"] = priority
    if district_id is not None:
        conditions.append("cz.district_id = :district_id")
        params["district_id"] = district_id

    stmt = text(f"""
        SELECT
            cz.zone_id,
            cz.centroid_lat,
            cz.centroid_lon,
            cz.priority_label,
            cz.recommendation_reason,
            cz.coverage_gap_m,
            cz.population_density,
            cz.road_density,
            cz.ml_score,
            cz.model_version,
            cz.inference_date,
            cz.income_stratum,
            d.district_name,
            ST_AsGeoJSON(cz.geometry) AS geometry_json
        FROM candidate_zones cz
        JOIN districts d ON d.district_id = cz.district_id
        WHERE {' AND '.join(conditions)}
        ORDER BY cz.ml_score DESC NULLS LAST
        LIMIT :limit
    """)

    rows = (await db.execute(stmt, params)).mappings().all()

    features = []
    for row in rows:
        geom = json.loads(row["geometry_json"]) if row["geometry_json"] else None
        props: dict = {
            "zone_id":                row["zone_id"],
            "priority_label":         row["priority_label"],
            "recommendation_reason":  row["recommendation_reason"],
            "coverage_gap_m":         row["coverage_gap_m"],
            "population_density":     row["population_density"],
            "road_density":           row["road_density"],
            "centroid_lat":           row["centroid_lat"],
            "centroid_lon":           row["centroid_lon"],
            "district_name":          row["district_name"],
            "income_stratum":         row["income_stratum"],
            "model_version":          row["model_version"],
            "inference_date": (
                row["inference_date"].isoformat() if row["inference_date"] else None
            ),
        }
        if include_ml_score:
            props["ml_score"] = row["ml_score"]
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    return {"type": "FeatureCollection", "features": features}


# ── Versionado de modelos ─────────────────────────────────────────────────────

async def get_all_models(db: AsyncSession) -> list[dict]:
    """Lista todas las versiones de modelo registradas, más recientes primero."""
    rows = (
        await db.execute(
            select(ModelVersion).order_by(ModelVersion.created_at.desc())
        )
    ).scalars().all()

    return [
        {
            "version_name":  r.version_name,
            "training_date": r.training_date.isoformat() if r.training_date else None,
            "is_active":     r.is_active,
            "artifact_url":  r.artifact_url,
            "metrics":       json.loads(r.metrics) if r.metrics else None,
            "created_at":    r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def get_model_metrics(db: AsyncSession, version_name: str) -> dict:
    """
    Métricas detalladas de una versión + comparativa con la versión anterior.
    La comparativa muestra el delta de cada métrica (positivo = mejora).
    """
    row = (
        await db.execute(
            select(ModelVersion).where(ModelVersion.version_name == version_name)
        )
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "MODEL_NOT_FOUND", "message": f"Versión '{version_name}' no encontrada."},
        )

    metrics = json.loads(row.metrics) if row.metrics else {}
    features = json.loads(row.features_used) if row.features_used else FEATURE_COLUMNS

    # Versión anterior (la más reciente antes de esta)
    prev_row = (
        await db.execute(
            select(ModelVersion)
            .where(ModelVersion.created_at < row.created_at)
            .order_by(ModelVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    comparison = None
    if prev_row:
        prev_metrics = json.loads(prev_row.metrics) if prev_row.metrics else {}
        comparison = {
            "version_name": prev_row.version_name,
            "metrics":      prev_metrics,
            "delta": {
                k: round(float(metrics.get(k, 0)) - float(prev_metrics.get(k, 0)), 4)
                for k in metrics
                if k in prev_metrics
            },
        }

    return {
        "version_name":             row.version_name,
        "training_date":            row.training_date.isoformat() if row.training_date else None,
        "is_active":                row.is_active,
        "artifact_url":             row.artifact_url,
        "metrics":                  metrics,
        "features_used":            features,
        "created_at":               row.created_at.isoformat() if row.created_at else None,
        "comparison_with_previous": comparison,
    }


async def activate_model(db: AsyncSession, version_name: str) -> dict:
    """
    Activa la versión indicada y desactiva todas las demás.
    Limpia el caché en memoria para que la próxima inferencia use el modelo activado.
    """
    row = (
        await db.execute(
            select(ModelVersion).where(ModelVersion.version_name == version_name)
        )
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "MODEL_NOT_FOUND", "message": f"Versión '{version_name}' no encontrada."},
        )

    # Desactivar todo → activar solo la versión solicitada
    await db.execute(update(ModelVersion).values(is_active=False))
    await db.execute(
        update(ModelVersion)
        .where(ModelVersion.version_name == version_name)
        .values(is_active=True)
    )
    await db.commit()

    # Limpiar caché para forzar recarga del modelo activado
    _model_cache.clear()
    logger.info("Modelo activado: %s. Caché limpiado.", version_name)

    return {
        "version_name": version_name,
        "is_active":    True,
        "message":      f"Versión '{version_name}' activada. La próxima inferencia usará este modelo.",
    }


async def register_model(
    db: AsyncSession,
    version_name: str,
    artifact_url: str | None,
    metrics: dict | None,
    features_used: list[str] | None,
    trained_by: int | None,
) -> dict:
    """
    Registra una nueva versión de modelo.
    Llamado por el script de entrenamiento de Nikole tras guardar el .pkl en Azure Blob.
    """
    existing = (
        await db.execute(
            select(ModelVersion).where(ModelVersion.version_name == version_name)
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "VERSION_EXISTS", "message": f"La versión '{version_name}' ya existe."},
        )

    new_ver = ModelVersion(
        version_name=version_name,
        training_date=datetime.utcnow(),
        trained_by=trained_by,
        artifact_url=artifact_url,
        metrics=json.dumps(metrics or {}),
        features_used=json.dumps(features_used or FEATURE_COLUMNS),
        is_active=False,
    )
    db.add(new_ver)
    await db.commit()
    await db.refresh(new_ver)

    return {
        "version_name":  new_ver.version_name,
        "training_date": new_ver.training_date.isoformat() if new_ver.training_date else None,
        "is_active":     new_ver.is_active,
        "artifact_url":  new_ver.artifact_url,
        "metrics":       json.loads(new_ver.metrics) if new_ver.metrics else {},
        "created_at":    new_ver.created_at.isoformat() if new_ver.created_at else None,
    }


async def count_inferable_zones(db: AsyncSession) -> int:
    not_null = [getattr(CandidateZone, col).isnot(None) for col in FEATURE_COLUMNS]
    result = await db.execute(
        select(func.count()).select_from(CandidateZone).where(and_(*not_null))
    )
    return result.scalar() or 0
