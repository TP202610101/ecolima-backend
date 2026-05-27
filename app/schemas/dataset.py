from pydantic import BaseModel
from typing import List


class DatasetResponse(BaseModel):
    dataset_id: int
    filename: str
    row_count: int | None
    detected_columns: list[str] | None
    status: str

    model_config = {"from_attributes": True}


class DatasetListItem(BaseModel):
    dataset_id: int
    filename: str
    uploaded_at: str
    row_count: int | None
    status: str

    model_config = {"from_attributes": True}
