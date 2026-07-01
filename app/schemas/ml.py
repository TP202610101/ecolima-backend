from pydantic import BaseModel, Field
from typing import Any


# ── Proxy hacia la API de ecolima-ml (contrato v0.3) ──────────────────────────

class MLZonePredictionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    zone: dict[str, Any]
    zone_id: str | int | None = None
    model_name: str | None = None
    include_explanation: bool = False


class MLBatchPredictionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    zones: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
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
