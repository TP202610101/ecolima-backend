from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Proxy hacia la API de ecolima-ml (contrato v0.3) ──────────────────────────
#
# Tipado según `contrato-ml-api.md` (auditoría de solo lectura del repo
# ecolima-ml, no ejecutada contra la API viva — instancia caída). Referencias
# de línea (`preprocessing.py:NN`, `schemas.py:NN`, etc.) apuntan a ese repo,
# no a este.
#
# Antes de esto, `zone: dict[str, Any]` no validaba nada — cualquier forma
# pasaba. Este archivo reemplaza eso por MLZoneFeatures, que sí puede fallar
# en validación si el shape no calza.


class MLTopFeature(BaseModel):
    """Una entrada de `top_features` en la respuesta de /predict (contrato §1)."""
    feature: str
    shap_value: float
    direction: Literal["positive", "negative"]


class MLPredictionResult(BaseModel):
    """Forma de un resultado individual — igual en /predict, dentro de
    /predict/batch.items y de /recommendations.items (contrato §1, §5)."""
    zone_id: str | int
    suitability_score: float
    is_recommended: bool
    top_features: list[MLTopFeature] = Field(default_factory=list)


class MLBatchPredictionResponse(BaseModel):
    """Respuesta de /predict/batch y /recommendations — ambas envuelven en
    'items' (contrato §1, §5)."""
    items: list[MLPredictionResult]


class MLModelMetadataResponse(BaseModel):
    """
    Respuesta de GET /model/metadata (contrato §5).
    `metadata` queda como dict libre a propósito: el contrato confirma que
    NO todos los modelos traen las mismas claves (el modelo baseline
    `lgbm_recycling` no tiene `serving_threshold`, `training_dataset` ni
    `test_metrics` — solo pasó por `trainer.py::_save_artifacts`, no por
    `scripts/run_simulated_training.py`). Tipar esto de forma rígida
    produciría falsos rechazos.
    `threshold` (top-level) es el que hay que usar — no `metadata.serving_threshold`,
    que es un duplicado y puede faltar según el modelo.
    """
    model_config = {"protected_namespaces": ()}
    model_name: str
    threshold: float
    metadata: dict[str, Any]


class MLHealthResponse(BaseModel):
    """Respuesta de GET /health (contrato §5). `model_path` es una ruta de
    filesystem del proceso remoto — no portable, solo para debug."""
    model_config = {"protected_namespaces": ()}
    status: str
    model_name: str
    model_loaded: bool
    model_path: str | None = None
    threshold: float | None = None
    target: str | None = None


class MLZoneFeatures(BaseModel):
    """
    Shape del dict `zone` que espera POST /predict (contrato §1, §3).

    Todas las features son opcionales aquí a propósito: hoy `candidate_zones`
    no tiene datos para varias de ellas (ver `ml_zone_mapping.py`) y esta
    clase debe poder representar tanto un payload completo (ej. el ejemplo
    real de `smoke_api_contract.py`, contrato §4) como uno parcial. La
    protección real de "no mandar lo que no debo" vive en `ml_zone_mapping.py`,
    no aquí — ver el comentario sobre accessibility_composite/nse_high_ratio/
    recycling_deficit más abajo.

    `extra = "allow"`: el contrato confirma que `zone` es un dict libre del
    lado de ecolima-ml (Pydantic no valida su forma interna, `schemas.py:12-16`)
    y que columnas no reconocidas se ignoran (`ColumnTransformer(remainder="drop")`).
    Mantenemos esa permisividad para no romper si el payload trae columnas de
    trazabilidad (ej. `candidate_id`, que el propio endpoint usa como fallback
    de zone_id — `app.py:34-43` — o metadata del CSV de origen).
    """
    model_config = {"extra": "allow"}

    # Identificador opcional embebido en el propio zone — contrato §1:
    # "zone_id = request.zone_id if ... else request.zone.get('candidate_id', 0)"
    candidate_id: str | int | None = None

    # ── Crudas (16, contrato §3) ────────────────────────────────────────────
    population_density: float | None = Field(default=None, ge=0)
    num_households: int | None = Field(default=None, ge=0)
    nse_ab_pct: float | None = Field(default=None, ge=0, le=1)
    nse_c_pct: float | None = Field(default=None, ge=0, le=1)
    nse_de_pct: float | None = Field(default=None, ge=0, le=1)
    urbanization_rate: float | None = Field(default=None, ge=0, le=1)
    dist_nearest_road_m: float | None = Field(default=None, ge=0)
    walkability_score: float | None = Field(default=None, ge=0, le=1)
    poi_commercial_500m: int | None = Field(default=None, ge=0)
    poi_educational_500m: int | None = Field(default=None, ge=0)
    poi_parks_500m: float | None = Field(default=None, ge=0)
    land_use_encoded: Literal[0, 1, 2, 3] | None = None
    area_m2: float | None = Field(default=None, ge=0)
    dist_nearest_recycling_m: float | None = Field(default=None, ge=0)
    recycling_density_1km: int | None = Field(default=None, ge=0)
    waste_per_capita_kg: float | None = Field(default=None, ge=0)

    # Puente de compatibilidad (contrato §2, §3, preprocessing.py:72-73):
    # si poi_parks_500m no viene y has_park_300m sí, el servidor deriva
    # poi_parks_500m = has_park_300m.astype(float). candidate_zones SÍ tiene
    # has_park_300m — mandamos este campo en vez de inventar poi_parks_500m.
    has_park_300m: bool | None = None

    # coverage_gap_index: "operativa" con fallback — se respeta tal cual si
    # se manda (contrato §2, preprocessing.py:68-69). No la mandamos desde el
    # mapeo (ver ml_zone_mapping.py) pero el schema la acepta si alguien la
    # provee explícitamente.
    coverage_gap_index: float | None = Field(default=None, ge=0, le=1)

    # ── Derivadas SIEMPRE recalculadas server-side si sus raw están presentes
    # (contrato §2, preprocessing.py:53-65) — CUALQUIER valor que se mande acá
    # se IGNORA en silencio, sin error ni warning ("silent override"). El
    # schema las acepta (para poder validar el payload de ejemplo real de
    # smoke_api_contract.py, contrato §4, que las trae) pero
    # ml_zone_mapping.py NUNCA las escribe — ver su docstring.
    accessibility_composite: float | None = Field(default=None, ge=0, le=1)
    nse_high_ratio: float | None = Field(default=None, ge=0, le=1)
    recycling_deficit: Literal[0, 1] | None = None


class MLZonePredictionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    zone: MLZoneFeatures
    zone_id: str | int | None = None
    model_name: str | None = None
    include_explanation: bool = False


class MLBatchPredictionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    zones: list[MLZoneFeatures] = Field(min_length=1, max_length=1000)
    zone_ids: list[str | int] | None = None
    model_name: str | None = None


class MLRecommendationRequest(MLBatchPredictionRequest):
    top_n: int = Field(default=10, ge=1, le=500)


class RecommendationResponse(BaseModel):
    zone_id: int
    ml_score: float
    is_recommended: bool
    priority_label: str
    recommendation_reason: str | None
    coverage_gap_m: float | None
    model_version: str | None
    inference_date: str | None

    model_config = {"from_attributes": True}
