import io
import json
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_role, get_current_user
from app.db.session import get_session
from app.models.user import User
from app.schemas.dataset import (
    DatasetCommitResponse,
    DatasetDeleteRowsRequest,
    DatasetDeleteRowsResponse,
    DatasetEditCellsRequest,
    DatasetEditCellsResponse,
    DatasetHistoryResponse,
    DatasetListItem,
    DatasetResponse,
    DatasetStatusUpdateRequest,
    DatasetValidationResponse,
)
from app.services.dataset_service import (
    commit_dataset,
    create_dataset_record,
    delete_dataset_rows,
    delete_incomplete_rows,
    edit_dataset_cells,
    export_dataset,
    get_dataset_history,
    get_datasets,
    log_action,
    read_dataset_file,
    save_uploaded_dataset_file,
    validate_dataset,
)

router = APIRouter()


@router.post("", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def upload_dataset(
    file: UploadFile = File(...),
    user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    dataframe, file_size_bytes, raw_bytes = await read_dataset_file(file)
    detected_columns = list(dataframe.columns.astype(str))
    row_count = len(dataframe)

    dataset = await create_dataset_record(
        db=db,
        filename=file.filename,
        original_filename=getattr(file, "filename", None),
        uploaded_by=user.user_id,
        file_size_bytes=file_size_bytes,
        row_count=row_count,
        detected_columns=detected_columns,
        status="pending",
    )

    save_uploaded_dataset_file(dataset.dataset_id, file.filename, raw_bytes)
    log_action(db, user.user_id, "upload", dataset.dataset_id, {"filename": file.filename, "row_count": row_count})
    await db.commit()

    return DatasetResponse(
        dataset_id=dataset.dataset_id,
        filename=dataset.filename,
        row_count=dataset.row_count,
        detected_columns=json.loads(dataset.detected_columns) if dataset.detected_columns else None,
        status=dataset.status,
    )


@router.get("/{dataset_id}/validate", response_model=DatasetValidationResponse)
async def validate_dataset_endpoint(
    dataset_id: int,
    user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    return await validate_dataset(db, dataset_id)


@router.delete("/{dataset_id}/rows", response_model=DatasetDeleteRowsResponse)
async def delete_dataset_rows_endpoint(
    dataset_id: int,
    payload: DatasetDeleteRowsRequest | None = None,
    row_status: str | None = Query(default=None, alias="status"),
    confirm: bool = False,
    user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """
    ?status=incomplete       -> elimina todas las filas incompletas, sin body.
    body {row_indices, reason} -> elimina las filas indicadas (comportamiento anterior).
    """
    if row_status == "incomplete":
        return await delete_incomplete_rows(db=db, dataset_id=dataset_id, user_id=user.user_id)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "MISSING_PAYLOAD",
                "message": "Enviar {row_indices, reason} en el body, o ?status=incomplete",
            },
        )

    return await delete_dataset_rows(
        db=db,
        dataset_id=dataset_id,
        row_indices=payload.row_indices,
        reason=payload.reason,
        user_id=user.user_id,
        confirm=confirm,
    )


@router.patch("/{dataset_id}/cells", response_model=DatasetEditCellsResponse)
async def edit_dataset_cells_endpoint(
    dataset_id: int,
    payload: DatasetEditCellsRequest,
    user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    return await edit_dataset_cells(db=db, dataset_id=dataset_id, edits=[edit.model_dump() for edit in payload.edits], user_id=user.user_id)


@router.get("", response_model=list[DatasetListItem])
async def list_datasets(
    skip: int = 0,
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    datasets = await get_datasets(db, skip=skip, limit=limit)
    response = []
    for dataset in datasets:
        response.append(
            DatasetListItem(
                dataset_id=dataset.dataset_id,
                filename=dataset.filename,
                uploaded_at=dataset.uploaded_at.isoformat() if dataset.uploaded_at else None,
                row_count=dataset.row_count,
                status=dataset.status,
            )
        )
    return response


@router.patch("/{dataset_id}", response_model=DatasetCommitResponse)
async def update_dataset_status_endpoint(
    dataset_id: int,
    payload: DatasetStatusUpdateRequest,
    user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """Único valor soportado hoy: {"status": "committed"} (reemplaza a POST /commit)."""
    if payload.status != "committed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "UNSUPPORTED_STATUS",
                "message": "Solo se soporta status='committed'",
                "allowed": ["committed"],
            },
        )
    result = await commit_dataset(db=db, dataset_id=dataset_id, user_id=user.user_id)
    return DatasetCommitResponse(**result)


@router.get("/{dataset_id}/export")
async def export_dataset_endpoint(
    dataset_id: int,
    format: str = "csv",
    user: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    file_bytes, filename = await export_dataset(db=db, dataset_id=dataset_id, fmt=format, user_id=user.user_id)
    if format == "xlsx":
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        media_type = "text/csv"
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{dataset_id}/history", response_model=DatasetHistoryResponse)
async def get_dataset_history_endpoint(
    dataset_id: int,
    user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    return await get_dataset_history(db=db, dataset_id=dataset_id)
