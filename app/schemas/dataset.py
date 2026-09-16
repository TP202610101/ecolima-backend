from pydantic import BaseModel


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


class DatasetValidationResponse(BaseModel):
    dataset_id: int
    valid: bool
    missing_columns: list[str]
    type_errors: list[dict]
    # Advertencia, NO bloquea 'valid' -- filas con las mismas coordenadas
    # exactas dentro del mismo archivo. El usuario decide si son puntos
    # legítimos o un error de carga.
    duplicate_rows: list[dict] = []
    row_count: int
    valid_rows: int
    error_rows: int

    model_config = {"from_attributes": True}


class DatasetDeleteResponse(BaseModel):
    deleted: bool
    dataset_id: int
    filename: str

    model_config = {"from_attributes": True}


class DatasetDeleteRowsRequest(BaseModel):
    row_indices: list[int]
    reason: str


class DatasetDeleteRowsResponse(BaseModel):
    deleted_count: int
    remaining_rows: int

    model_config = {"from_attributes": True}


class DatasetEditCell(BaseModel):
    row_index: int
    column: str
    new_value: object


class DatasetEditCellsRequest(BaseModel):
    edits: list[DatasetEditCell]


class DatasetEditCellsResponse(BaseModel):
    edited_count: int
    edits_applied: list[dict]

    model_config = {"from_attributes": True}


class DatasetStatusUpdateRequest(BaseModel):
    status: str


class DatasetCommitResponse(BaseModel):
    inserted: int
    skipped_duplicates: int
    errors: list[dict]


class AuditEntry(BaseModel):
    audit_id: int
    action: str
    user_id: int
    created_at: str | None
    details: dict | None = None


class DatasetHistoryResponse(BaseModel):
    dataset_id: int
    filename: str
    uploads: list[AuditEntry]
    validations: list[AuditEntry]
    edits: list[AuditEntry]
    commit: AuditEntry | None
    exported_at: str | None
