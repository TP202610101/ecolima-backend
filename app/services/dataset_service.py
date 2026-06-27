import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException, status, UploadFile
from geoalchemy2.elements import WKTElement
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.dataset import Dataset
from app.models.recycling_point import RecyclingPoint

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


def _write_dataset_file(dataset_id: int, df: pd.DataFrame) -> Path:
    path = _get_dataset_file_path(dataset_id)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archivo del dataset no encontrado.",
        )

    if path.suffix.lower() == ".csv":
        df.to_csv(path, index=False, encoding="utf-8")
    elif path.suffix.lower() == ".xlsx":
        df.to_excel(path, index=False, engine="openpyxl")
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato de archivo de dataset no soportado.",
        )

    return path


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.replace(r"^\s*$", pd.NA, regex=True)


def _coerce_int(value: Any) -> int:
    if value is None or pd.isna(value):
        raise ValueError("Valor faltante")
    if isinstance(value, (int, float)) and float(value).is_integer():
        return int(value)
    if isinstance(value, str):
        if value.strip() == "":
            raise ValueError("Valor faltante")
        return int(value)
    raise ValueError("No convertible a entero")


def _validate_edit_value(column: str, value: Any) -> Any:
    if column == "latitude":
        if value is None or pd.isna(value):
            raise ValueError("latitude no puede ser nulo")
        latitude = float(value)
        if not (-13.0 <= latitude <= -11.5):
            raise ValueError("latitude debe estar entre -13.0 y -11.5")
        return latitude

    if column == "longitude":
        if value is None or pd.isna(value):
            raise ValueError("longitude no puede ser nulo")
        longitude = float(value)
        if not (-77.5 <= longitude <= -76.5):
            raise ValueError("longitude debe estar entre -77.5 y -76.5")
        return longitude

    if column == "district_id":
        return _coerce_int(value)

    if column == "verified":
        if value is None or pd.isna(value):
            return None
        return _coerce_bool(value)

    return value


def _create_audit_log_entry(
    db: AsyncSession,
    user_id: int,
    action: str,
    dataset_id: int | None,
    details: dict[str, Any] | None = None,
) -> None:
    audit = AuditLog(
        user_id=user_id,
        action=action,
        dataset_id=dataset_id,
        details=json.dumps(details or {}, ensure_ascii=False, default=str),
    )
    db.add(audit)


async def _persist_dataset_changes(db: AsyncSession, dataset: Dataset, row_count: int | None = None) -> None:
    if row_count is not None:
        dataset.row_count = row_count
    dataset.status = "pending"
    dataset.error_summary = None
    await db.commit()
    await db.refresh(dataset)


async def delete_dataset_rows(
    db: AsyncSession,
    dataset_id: int,
    row_indices: list[int],
    reason: str,
    user_id: int,
    confirm: bool = False,
) -> dict:
    if not row_indices:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Debe proveer al menos un row_index para eliminar.",
        )

    dataset = await get_dataset_by_id(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado.")

    df, _ = _load_dataset_file(dataset_id)
    invalid_indices = [idx for idx in row_indices if idx not in list(df.index)]
    if invalid_indices:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"invalid_row_indices": invalid_indices},
        )

    deleted_count = len(set(row_indices))
    remaining_rows = len(df) - deleted_count

    if not confirm:
        return {"deleted_count": deleted_count, "remaining_rows": remaining_rows}

    df = df.drop(index=row_indices).reset_index(drop=True)
    _write_dataset_file(dataset_id, df)
    await _persist_dataset_changes(db, dataset, row_count=len(df))
    _create_audit_log_entry(
        db,
        user_id=user_id,
        action="delete_rows",
        dataset_id=dataset_id,
        details={
            "row_indices": sorted(set(row_indices)),
            "reason": reason,
            "deleted_count": deleted_count,
        },
    )
    await db.commit()

    return {"deleted_count": deleted_count, "remaining_rows": len(df)}


async def delete_incomplete_rows(db: AsyncSession, dataset_id: int, user_id: int) -> dict:
    dataset = await get_dataset_by_id(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado.")

    df, _ = _load_dataset_file(dataset_id)
    required = ["latitude", "longitude", "district_id"]
    missing_columns = [col for col in required if col not in df.columns]
    if missing_columns:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"missing_columns": missing_columns},
        )

    df = _normalize_dataframe(df)
    mask = (
        df["latitude"].isna() | df["longitude"].isna() | df["district_id"].isna()
    )
    deleted_count = int(mask.sum())
    if deleted_count > 0:
        df = df[~mask].reset_index(drop=True)
        _write_dataset_file(dataset_id, df)
        await _persist_dataset_changes(db, dataset, row_count=len(df))
        _create_audit_log_entry(
            db,
            user_id=user_id,
            action="delete_incomplete_rows",
            dataset_id=dataset_id,
            details={"deleted_count": deleted_count},
        )
        await db.commit()

    return {"deleted_count": deleted_count, "remaining_rows": len(df)}


async def edit_dataset_cells(
    db: AsyncSession,
    dataset_id: int,
    edits: list[dict],
    user_id: int,
) -> dict:
    dataset = await get_dataset_by_id(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado.")

    df, _ = _load_dataset_file(dataset_id)
    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El dataset no contiene filas para editar.",
        )

    applied: list[dict[str, Any]] = []
    for edit in edits:
        row_index = edit.get("row_index")
        column = edit.get("column")
        new_value = edit.get("new_value")

        if row_index not in list(df.index):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"row_index": row_index, "error": "Índice de fila no existe."},
            )
        if column not in df.columns:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"column": column, "error": "Columna no existe en el dataset."},
            )

        try:
            validated_value = _validate_edit_value(column, new_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"row_index": row_index, "column": column, "error": str(exc)},
            )

        old_value = df.at[row_index, column]
        df.at[row_index, column] = validated_value
        applied.append(
            {
                "row_index": row_index,
                "column": column,
                "old_value": None if pd.isna(old_value) else old_value,
                "new_value": None if pd.isna(validated_value) else validated_value,
            }
        )

    _write_dataset_file(dataset_id, df)
    await _persist_dataset_changes(db, dataset)

    for edit_record in applied:
        _create_audit_log_entry(
            db,
            user_id=user_id,
            action="edit_cell",
            dataset_id=dataset_id,
            details=edit_record,
        )
    await db.commit()

    return {"edited_count": len(applied), "edits_applied": applied}


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


def log_action(
    db: AsyncSession,
    user_id: int,
    action: str,
    dataset_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    _create_audit_log_entry(db, user_id, action, dataset_id, details)


async def commit_dataset(db: AsyncSession, dataset_id: int, user_id: int) -> dict:
    dataset = await get_dataset_by_id(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado.")

    if dataset.status == "committed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ALREADY_COMMITTED", "message": "El dataset ya fue comprometido."},
        )

    df, _ = _load_dataset_file(dataset_id)
    missing = _validate_columns(df)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_COLUMNS", "message": "Faltan columnas obligatorias", "missing": missing},
        )

    df = _normalize_dataframe(df)

    dist_result = await db.execute(text("SELECT district_id FROM districts"))
    valid_district_ids = {row[0] for row in dist_result.fetchall()}

    inserted = 0
    skipped_duplicates = 0
    errors: list[dict] = []

    for index, row in df.iterrows():
        row_index = int(index)

        if (
            pd.isna(row.get("latitude"))
            or pd.isna(row.get("longitude"))
            or pd.isna(row.get("district_id"))
            or pd.isna(row.get("source"))
        ):
            errors.append({"row_index": row_index, "error": "Campos obligatorios con valor nulo"})
            continue

        try:
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
            district_id = int(float(str(row["district_id"])))
            source = str(row["source"]).strip()
        except (TypeError, ValueError) as exc:
            errors.append({"row_index": row_index, "error": str(exc)})
            continue

        if district_id not in valid_district_ids:
            errors.append({"row_index": row_index, "error": f"district_id {district_id} no encontrado"})
            continue

        dup = await db.execute(
            text(
                "SELECT point_id FROM recycling_points "
                "WHERE ABS(latitude - :lat) < 0.0001 AND ABS(longitude - :lon) < 0.0001 LIMIT 1"
            ),
            {"lat": latitude, "lon": longitude},
        )
        if dup.first() is not None:
            skipped_duplicates += 1
            continue

        def _opt(col: str) -> str | None:
            if col not in df.columns:
                return None
            val = row[col]
            return None if pd.isna(val) else str(val).strip() or None

        verified_val: bool = False
        if "verified" in df.columns and not pd.isna(row["verified"]):
            try:
                coerced = _coerce_bool(row["verified"])
                verified_val = bool(coerced) if coerced is not None else False
            except ValueError:
                verified_val = False

        point = RecyclingPoint(
            district_id=district_id,
            latitude=latitude,
            longitude=longitude,
            geometry=WKTElement(f"POINT({longitude} {latitude})", srid=4326),
            point_type=_opt("point_type"),
            address=_opt("address"),
            operator=_opt("operator"),
            materials_accepted=_opt("materials_accepted"),
            verified=verified_val,
            source=source,
        )
        db.add(point)
        inserted += 1

    dataset.status = "committed" if inserted > 0 else "failed"
    _create_audit_log_entry(
        db,
        user_id,
        "commit",
        dataset_id,
        {"inserted": inserted, "skipped_duplicates": skipped_duplicates, "error_count": len(errors)},
    )
    await db.commit()

    return {"inserted": inserted, "skipped_duplicates": skipped_duplicates, "errors": errors}


async def export_dataset(
    db: AsyncSession, dataset_id: int, fmt: str, user_id: int
) -> tuple[bytes, str]:
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El parámetro format debe ser 'csv' o 'xlsx'.",
        )

    dataset = await get_dataset_by_id(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado.")

    df, _ = _load_dataset_file(dataset_id)

    now = datetime.utcnow()
    df["exported_at"] = now.isoformat()

    date_str = now.strftime("%Y-%m-%d")
    base_name = Path(dataset.original_filename or dataset.filename).stem

    if fmt == "xlsx":
        out = io.BytesIO()
        df.to_excel(out, index=False, engine="openpyxl")
        out.seek(0)
        file_bytes = out.read()
        filename = f"{base_name}_limpio_{date_str}.xlsx"
    else:
        file_bytes = df.to_csv(index=False, encoding="utf-8").encode("utf-8")
        filename = f"{base_name}_limpio_{date_str}.csv"

    _create_audit_log_entry(db, user_id, "export", dataset_id, {"format": fmt, "exported_at": now.isoformat()})
    await db.commit()

    return file_bytes, filename


async def get_dataset_history(db: AsyncSession, dataset_id: int) -> dict:
    dataset = await get_dataset_by_id(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado.")

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.dataset_id == dataset_id)
        .order_by(AuditLog.created_at.asc())
    )
    logs = result.scalars().all()

    def _entry(log: AuditLog) -> dict:
        details = None
        if log.details:
            try:
                details = json.loads(log.details)
            except (json.JSONDecodeError, TypeError):
                details = {"raw": log.details}
        return {
            "audit_id": log.audit_id,
            "action": log.action,
            "user_id": log.user_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "details": details,
        }

    uploads = [_entry(l) for l in logs if l.action == "upload"]
    validations = [_entry(l) for l in logs if l.action in ("validate", "validation")]
    edits = [_entry(l) for l in logs if l.action in ("edit_cell", "delete_rows", "delete_incomplete_rows")]
    commit_entries = [_entry(l) for l in logs if l.action == "commit"]
    export_entries = [_entry(l) for l in logs if l.action == "export"]

    return {
        "dataset_id": dataset.dataset_id,
        "filename": dataset.filename,
        "uploads": uploads,
        "validations": validations,
        "edits": edits,
        "commit": commit_entries[-1] if commit_entries else None,
        "exported_at": export_entries[-1]["created_at"] if export_entries else None,
    }
