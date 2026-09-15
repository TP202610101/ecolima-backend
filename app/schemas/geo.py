from typing import Any

from pydantic import BaseModel


class GeoJSONFeature(BaseModel):
    type: str
    geometry: dict[str, Any]
    properties: dict[str, Any]


class GeoJSONFeatureCollection(BaseModel):
    type: str
    features: list[GeoJSONFeature]
