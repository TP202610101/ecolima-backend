import io
import json
from pathlib import Path

import pandas as pd
from fastapi import HTTPException, status, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Dataset

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}


def _get_file_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def _validate_file_extension(filename: str) -> None:
    ext = _get_file_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Solo se permiten archivos CSV y XLSX.",
        )


def _validate_file_size(file_size: int) -> None:
    if file_size > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="El archivo excede el tamaño máximo permitido de 20MB.",
        )


def _read_csv_bytes(raw_bytes: bytes) -> pd.DataFrame:
    encodings = ["utf-8", "utf-8-sig", "utf-16", "latin-1"]
    last_exc: Exception | None = None

    for encoding in encodings:
        try:
            text = raw_bytes.decode(encoding)
            return pd.read_csv(io.StringIO(text))
        except Exception as exc:
            last_exc = exc

    raise UnicodeDecodeError(
        "utf-8",
        raw_bytes,
        0,
        1,
        f"No se pudo decodificar el CSV con ninguno de los encodings soportados: {encodings}. Último error: {last_exc}"
    )


async def read_dataset_file(upload_file: UploadFile) -> tuple[pd.DataFrame, int]:
    _validate_file_extension(upload_file.filename)

    raw_bytes = await upload_file.read()
    file_size = len(raw_bytes)
    _validate_file_size(file_size)

    extension = _get_file_extension(upload_file.filename)

    try:
        if extension == ".csv":
            df = _read_csv_bytes(raw_bytes)
        else:
            buffer = io.BytesIO(raw_bytes)
            df = pd.read_excel(buffer, engine="openpyxl")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Error al leer el archivo: {exc}",
        )

    return df, file_size


async def create_dataset_record(
    db: AsyncSession,
    filename: str,
    original_filename: str | None,
    uploaded_by: int | None,
    file_size_bytes: int | None,
    row_count: int | None,
    detected_columns: list[str] | None,
    status: str = "pending",
) -> Dataset:
    dataset = Dataset(
        filename=filename,
        original_filename=original_filename,
        uploaded_by=uploaded_by,
        file_size_bytes=file_size_bytes,
        row_count=row_count,
        status=status,
        detected_columns=json.dumps(detected_columns) if detected_columns is not None else None,
    )
    db.add(dataset)
    await db.commit()
    await db.refresh(dataset)
    return dataset


async def get_datasets(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[Dataset]:
    result = await db.execute(
        select(Dataset).order_by(Dataset.uploaded_at.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()
