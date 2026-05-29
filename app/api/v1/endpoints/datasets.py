import json
from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_role, get_current_user
from app.db.session import get_session
from app.models.user import User
from app.schemas.dataset import DatasetListItem, DatasetResponse, DatasetValidationResponse
from app.services.dataset_service import (
    create_dataset_record,
    get_datasets,
    read_dataset_file,
    save_uploaded_dataset_file,
    validate_dataset,
)

router = APIRouter()


@router.post("/upload", response_model=DatasetResponse)
async def upload_dataset(
    file: UploadFile = File(...),
    user: User = Depends(require_role("admin", "cientifico")),
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
    user: User = Depends(require_role("admin", "cientifico")),
    db: AsyncSession = Depends(get_session),
):
    return await validate_dataset(db, dataset_id)


@router.get("/", response_model=list[DatasetListItem])
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
