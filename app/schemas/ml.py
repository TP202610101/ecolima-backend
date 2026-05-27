from pydantic import BaseModel
from typing import Any


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
