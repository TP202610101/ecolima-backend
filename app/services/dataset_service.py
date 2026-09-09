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
from app.services.blob_storage import is_configured, get_container_client

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
UPLOAD_DIR = Path("uploads/datasets")
REQUIRED_COLUMNS = ["latitude", "longitude", "district_id", "source"]
OPTIONAL_COLUMNS = ["point_type", "address", "operator", "materials_accepted", "verified"]

# Bounding box plano de Lima Metropolitana -- usado por /validate, la edición
# de celdas, Y por el commit (defensa en profundidad: el commit ya no confía
# únicamente en que /validate se haya corrido antes).
LAT_RANGE = (-13.0, -11.5)
LON_RANGE = (-77.5, -76.5)


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


def _blob_key(dataset_id: int, ext: str) -> str:
    return f"{dataset_id}{ext}"


def _datasets_container():
    """Cliente del contenedor de datasets, o None si Blob no está configurado
    (placeholder o variable ausente) -- en ese caso el llamador cae a disco local."""
    from app.core.config import Settings
    settings = Settings()
    if not is_configured(settings.azure_blob_connection_string):
        return None
    return get_container_client(settings.azure_blob_connection_string, settings.azure_blob_container_datasets)


def _get_dataset_file_path(dataset_id: int) -> Path | None:
    for ext in ALLOWED_EXTENSIONS:
        candidate = UPLOAD_DIR / f"{dataset_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def _get_dataset_blob(dataset_id: int, container):
    """Devuelve el blob_client del primer archivo existente del dataset, o None."""
    for ext in ALLOWED_EXTENSIONS:
        blob_client = container.get_blob_client(_blob_key(dataset_id, ext))
        if blob_client.exists():
            return blob_client
    return None


def _save_uploaded_dataset_file(dataset_id: int, filename: str, raw_bytes: bytes) -> str:
    ext = _get_file_extension(filename)
    container = _datasets_container()
    if container is not None:
        key = _blob_key(dataset_id, ext)
        container.upload_blob(name=key, data=raw_bytes, overwrite=True)
        return key

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{dataset_id}{ext}"
    path.write_bytes(raw_bytes)
    return str(path)


def save_uploaded_dataset_file(dataset_id: int, filename: str, raw_bytes: bytes) -> str:
    return _save_uploaded_dataset_file(dataset_id, filename, raw_bytes)


def delete_dataset_file(dataset_id: int) -> None:
    """Borra el archivo del dataset -- del contenedor de Blob si está configurado,
    o de disco local en su defecto. Usada por la eliminación de datasets y por
    el fixture de limpieza de tests (cleanup_datasets)."""
    container = _datasets_container()
    if container is not None:
        blob_client = _get_dataset_blob(dataset_id, container)
        if blob_client is not None:
            blob_client.delete_blob()
        return

    for ext in ALLOWED_EXTENSIONS:
        (UPLOAD_DIR / f"{dataset_id}{ext}").unlink(missing_ok=True)


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


def _parse_dataset_bytes(raw_bytes: bytes, extension: str) -> pd.DataFrame:
    try:
        if extension == ".csv":
            return _read_csv_bytes(raw_bytes)
        return pd.read_excel(io.BytesIO(raw_bytes), engine="openpyxl")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Error al leer el archivo: {exc}",
        )


def _serialize_dataset(df: pd.DataFrame, extension: str) -> bytes:
    if extension == ".csv":
        return df.to_csv(index=False, encoding="utf-8").encode("utf-8")
    if extension == ".xlsx":
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False, engine="openpyxl")
        return buffer.getvalue()
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Formato de archivo de dataset no soportado.",
    )


def _load_dataset_file(dataset_id: int) -> tuple[pd.DataFrame, int]:
    container = _datasets_container()
    if container is not None:
        blob_client = _get_dataset_blob(dataset_id, container)
        if blob_client is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Archivo del dataset no encontrado.",
            )
        raw_bytes = blob_client.download_blob().readall()
        extension = Path(blob_client.blob_name).suffix.lower()
        return _parse_dataset_bytes(raw_bytes, extension), len(raw_bytes)

    path = _get_dataset_file_path(dataset_id)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archivo del dataset no encontrado.",
        )

    raw_bytes = path.read_bytes()
    return _parse_dataset_bytes(raw_bytes, path.suffix.lower()), len(raw_bytes)


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


def _is_blank(value) -> bool:
    if pd.isna(value):
        return True
    return isinstance(value, str) and value.strip() == ""


def _validate_types(df: pd.DataFrame) -> list[dict]:
    errors: list[dict] = []
    if df.empty:
        return errors

    for index, row in df.iterrows():
        row_index = int(index)

        for column, min_val, max_val in [
            ("latitude", *LAT_RANGE),
            ("longitude", *LON_RANGE),
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

        for column in ("district_id", "source"):
            if column not in df.columns:
                continue
            value = row[column]
            if _is_blank(value):
                errors.append({"row_index": row_index, "column": column, "value": value, "error": "Valor faltante"})

        if "verified" in df.columns:
            value = row["verified"]
            if not pd.isna(value):
                try:
                    _coerce_bool(value)
                except ValueError:
                    errors.append({"row_index": row_index, "column": "verified", "value": value, "error": "Debe ser bool o convertible a bool"})

    return errors


def _find_duplicate_coordinate_rows(df: pd.DataFrame) -> list[dict]:
    """Filas con exactamente las mismas coordenadas (lat, lon) dentro del
    mismo archivo. Es ADVERTENCIA, no bloquea 'valid' -- el usuario decide si
    son puntos legítimamente pegados o un error de carga."""
    if df.empty or "latitude" not in df.columns or "longitude" not in df.columns:
        return []

    groups: dict[tuple[float, float], list[int]] = {}
    for index, row in df.iterrows():
        lat, lon = row.get("latitude"), row.get("longitude")
        if pd.isna(lat) or pd.isna(lon):
            continue
        try:
            key = (round(float(lat), 6), round(float(lon), 6))
        except (TypeError, ValueError):
            continue
        groups.setdefault(key, []).append(int(index))

    return [
        {"latitude": lat, "longitude": lon, "row_indices": indices}
        for (lat, lon), indices in groups.items()
        if len(indices) > 1
    ]


def _write_dataset_file(dataset_id: int, df: pd.DataFrame) -> None:
    container = _datasets_container()
    if container is not None:
        blob_client = _get_dataset_blob(dataset_id, container)
        if blob_client is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Archivo del dataset no encontrado.",
            )
        extension = Path(blob_client.blob_name).suffix.lower()
        blob_client.upload_blob(_serialize_dataset(df, extension), overwrite=True)
        return

    path = _get_dataset_file_path(dataset_id)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archivo del dataset no encontrado.",
        )
    path.write_bytes(_serialize_dataset(df, path.suffix.lower()))


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
        if not (LAT_RANGE[0] <= latitude <= LAT_RANGE[1]):
            raise ValueError(f"latitude debe estar entre {LAT_RANGE[0]} y {LAT_RANGE[1]}")
        return latitude

    if column == "longitude":
        if value is None or pd.isna(value):
            raise ValueError("longitude no puede ser nulo")
        longitude = float(value)
        if not (LON_RANGE[0] <= longitude <= LON_RANGE[1]):
            raise ValueError(f"longitude debe estar entre {LON_RANGE[0]} y {LON_RANGE[1]}")
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


def _reject_if_committed(dataset: Dataset) -> None:
    """Un dataset 'committed' es inmutable: editarlo/borrar filas después de
    comprometido reseteaba status a 'pending' y anulaba el guard
    ALREADY_COMMITTED de commit_dataset, permitiendo re-comprometer y crear
    puntos duplicados/huérfanos en recycling_points (ver auditoria-flujo-datasets.md)."""
    if dataset.status == "committed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DATASET_COMMITTED",
                "message": "El dataset ya fue comprometido y es inmutable. "
                           "No se puede editar ni borrar filas de un dataset 'committed'.",
            },
        )


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
    _reject_if_committed(dataset)

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
    _reject_if_committed(dataset)

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
    _reject_if_committed(dataset)

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


async def validate_dataset(db: AsyncSession, dataset_id: int, user_id: int) -> dict:
    dataset = await get_dataset_by_id(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado.")

    df, _ = _load_dataset_file(dataset_id)
    missing_columns = _validate_columns(df)
    type_errors = []
    duplicate_rows: list[dict] = []
    if not missing_columns:
        type_errors = _validate_types(df)
        duplicate_rows = _find_duplicate_coordinate_rows(df)

    # Los duplicados son ADVERTENCIA -- no cuentan para valid/error_rows. El
    # usuario decide si son puntos legítimos o un error de carga (ver
    # _find_duplicate_coordinate_rows).
    valid = not missing_columns and not type_errors
    error_summary = None
    if not valid:
        error_summary = json.dumps({"missing_columns": missing_columns, "type_errors": type_errors})

    dataset.status = "valid" if valid else "invalid"
    dataset.error_summary = error_summary

    if missing_columns:
        error_rows = len(df)
        valid_rows = 0
    else:
        distinct_error_rows = len({err["row_index"] for err in type_errors})
        error_rows = distinct_error_rows
        valid_rows = len(df) - distinct_error_rows

    # Registrar SIEMPRE el intento de validación (válido o inválido) -- antes
    # no se logueaba nunca, así que /history nunca mostraba "validations".
    # Mismo patrón que las demás acciones del flujo (edit_cell, commit, etc.):
    # solo counts/resumen, no las listas completas de errores fila por fila.
    _create_audit_log_entry(
        db,
        user_id=user_id,
        action="validate",
        dataset_id=dataset_id,
        details={
            "valid": valid,
            "row_count": len(df),
            "valid_rows": valid_rows,
            "error_rows": error_rows,
            "missing_columns": missing_columns,
            "type_error_count": len(type_errors),
            "duplicate_row_count": len(duplicate_rows),
        },
    )
    await db.commit()
    await db.refresh(dataset)

    return {
        "dataset_id": dataset.dataset_id,
        "valid": valid,
        "missing_columns": missing_columns,
        "type_errors": type_errors,
        "duplicate_rows": duplicate_rows,
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

    if dataset.status != "valid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NOT_VALIDATED",
                "message": f"El dataset debe validarse (GET /datasets/{dataset_id}/validate) y quedar en "
                           f"estado 'valid' antes de comprometerse. Estado actual: '{dataset.status}'.",
            },
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

        # Defensa en profundidad: repite el chequeo de rango que hace /validate.
        # El gate de status=='valid' de arriba ya debería garantizar esto, pero
        # no confiamos únicamente en eso -- una fila fuera de Lima nunca debe
        # llegar a recycling_points.
        if not (LAT_RANGE[0] <= latitude <= LAT_RANGE[1]):
            errors.append({"row_index": row_index, "error": f"latitude fuera de rango ({LAT_RANGE[0]} a {LAT_RANGE[1]})"})
            continue
        if not (LON_RANGE[0] <= longitude <= LON_RANGE[1]):
            errors.append({"row_index": row_index, "error": f"longitude fuera de rango ({LON_RANGE[0]} a {LON_RANGE[1]})"})
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


async def delete_dataset(db: AsyncSession, dataset_id: int, user_id: int) -> dict:
    """Elimina un dataset COMPLETO (archivo + registro) -- no solo filas
    dentro de él (para eso está delete_dataset_rows). Mismo principio de
    inmutabilidad que PATCH /cells y DELETE /rows: un dataset 'committed' no
    se puede borrar desde acá (_reject_if_committed, 409 DATASET_COMMITTED).
    Si hace falta revertir puntos reales que vinieron de un dataset ya
    comprometido, eso es un runbook de SQL directo sobre recycling_points,
    nunca esta acción -- un commit es de una sola vía.

    audit_log: la FK audit_log.dataset_id -> datasets.dataset_id tiene
    ON DELETE SET NULL (alembic/versions/001_initial.py:165) -- borrar el
    dataset NO borra su historial de auditoría, Postgres solo pone
    dataset_id=NULL en esas filas. Es la decisión correcta: el historial ya
    construido (upload/validate/edit/commit) sigue teniendo valor como
    evidencia de lo que se hizo, incluso de un dataset que ya no existe. Por
    eso esta misma entrada de auditoría de la eliminación repite dataset_id
    y filename dentro de `details` -- esa copia sobrevive aunque la columna
    FK termine en NULL por el mismo mecanismo.
    """
    dataset = await get_dataset_by_id(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado.")
    _reject_if_committed(dataset)

    filename = dataset.filename
    original_filename = dataset.original_filename
    status_before_delete = dataset.status

    delete_dataset_file(dataset_id)

    _create_audit_log_entry(
        db,
        user_id=user_id,
        action="delete_dataset",
        dataset_id=dataset_id,
        details={
            "dataset_id": dataset_id,
            "filename": filename,
            "original_filename": original_filename,
            "status_before_delete": status_before_delete,
        },
    )
    await db.delete(dataset)
    await db.commit()

    return {"deleted": True, "dataset_id": dataset_id, "filename": filename}


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
