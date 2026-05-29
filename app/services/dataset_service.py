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
UPLOAD_DIR = Path("uploads/datasets")
REQUIRED_COLUMNS = ["latitude", "longitude", "district_id", "source"]
OPTIONAL_COLUMNS = ["point_type", "address", "operator", "materials_accepted", "verified"]


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
        f"No se pudo decodificar el CSV con ninguno de los encodings soportados: {encodings}. Último error: {last_exc}",
    )


def _get_dataset_file_path(dataset_id: int) -> Path | None:
    for ext in ALLOWED_EXTENSIONS:
        candidate = UPLOAD_DIR / f"{dataset_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def _save_uploaded_dataset_file(dataset_id: int, filename: str, raw_bytes: bytes) -> Path:
    ext = _get_file_extension(filename)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{dataset_id}{ext}"
    path.write_bytes(raw_bytes)
    return path


def save_uploaded_dataset_file(dataset_id: int, filename: str, raw_bytes: bytes) -> Path:
    return _save_uploaded_dataset_file(dataset_id, filename, raw_bytes)


async def read_dataset_file(upload_file: UploadFile) -> tuple[pd.DataFrame, int, bytes]:
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

    return df, file_size, raw_bytes


def _load_dataset_file(dataset_id: int) -> tuple[pd.DataFrame, int]:
    path = _get_dataset_file_path(dataset_id)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archivo del dataset no encontrado.",
        )

    raw_bytes = path.read_bytes()
    extension = path.suffix.lower()

    try:
        if extension == ".csv":
            df = _read_csv_bytes(raw_bytes)
        else:
            df = pd.read_excel(io.BytesIO(raw_bytes), engine="openpyxl")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Error al leer el archivo: {exc}",
        )

    return df, len(raw_bytes)


def _coerce_bool(value) -> bool | None:
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(int(value))
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "verdadero", "1"):
            return True
        if normalized in ("false", "falso", "0"):
            return False
    raise ValueError("No convertible a booleano")


def _validate_columns(df: pd.DataFrame) -> list[str]:
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    return missing


def _validate_types(df: pd.DataFrame) -> list[dict]:
    errors: list[dict] = []
    if df.empty:
        return errors

    for index, row in df.iterrows():
        row_index = int(index)

        for column, min_val, max_val in [
            ("latitude", -13.0, -11.5),
            ("longitude", -77.5, -76.5),
        ]:
            if column not in df.columns:
                continue
            value = row[column]
            if pd.isna(value):
                errors.append({"row_index": row_index, "column": column, "value": value, "error": "Valor faltante"})
                continue
            try:
                num_value = float(value)
            except (TypeError, ValueError):
                errors.append({"row_index": row_index, "column": column, "value": value, "error": "Debe ser numérico"})
                continue

            if not (min_val <= num_value <= max_val):
                errors.append({"row_index": row_index, "column": column, "value": num_value, "error": f"Debe estar entre {min_val} y {max_val}."})

        if "verified" in df.columns:
            value = row["verified"]
            if not pd.isna(value):
                try:
                    _coerce_bool(value)
                except ValueError:
                    errors.append({"row_index": row_index, "column": "verified", "value": value, "error": "Debe ser bool o convertible a bool"})

    return errors


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


async def get_dataset_by_id(db: AsyncSession, dataset_id: int) -> Dataset | None:
    result = await db.execute(select(Dataset).where(Dataset.dataset_id == dataset_id))
    return result.scalar_one_or_none()


async def validate_dataset(db: AsyncSession, dataset_id: int) -> dict:
    dataset = await get_dataset_by_id(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado.")

    df, _ = _load_dataset_file(dataset_id)
    missing_columns = _validate_columns(df)
    type_errors = []
    if not missing_columns:
        type_errors = _validate_types(df)

    valid = not missing_columns and not type_errors
    error_summary = None
    if not valid:
        error_summary = json.dumps({"missing_columns": missing_columns, "type_errors": type_errors})

    dataset.status = "valid" if valid else "invalid"
    dataset.error_summary = error_summary
    await db.commit()
    await db.refresh(dataset)

    if missing_columns:
        error_rows = len(df)
        valid_rows = 0
    else:
        distinct_error_rows = len({err["row_index"] for err in type_errors})
        error_rows = distinct_error_rows
        valid_rows = len(df) - distinct_error_rows

    return {
        "dataset_id": dataset.dataset_id,
        "valid": valid,
        "missing_columns": missing_columns,
        "type_errors": type_errors,
        "row_count": len(df),
        "valid_rows": valid_rows,
        "error_rows": error_rows,
    }


async def get_datasets(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[Dataset]:
    result = await db.execute(
        select(Dataset).order_by(Dataset.uploaded_at.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()
