import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_zone import CandidateZone

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
