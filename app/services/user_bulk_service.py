import csv
import secrets
from collections import Counter
from typing import Awaitable, Callable

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import get_password_hash
from app.models.user import User
from app.schemas.user_bulk import (
    BulkConfirmResponse,
    BulkInvitationFailure,
    BulkRowError,
    BulkUserRow,
    BulkValidationResponse,
)
from app.services.dataset_service import log_action
from app.services.password_reset_service import create_invitation_token

InvitationSender = Callable[[str, str], Awaitable[None]]

MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB
MAX_DATA_ROWS = 200
# Único rol que se puede crear en bloque -- "admin" (o cualquier otro valor)
# siempre es ROL_NO_PERMITIDO. Mismo principio que ALLOWED_ROLES en
# endpoints/users.py: una vía de carga masiva no debería poder otorgar el
# rol más privilegiado.
BULK_ALLOWED_ROLE = "analista"
REQUIRED_HEADER = ("correo", "nombre", "rol")

_FILE_ERRORS = {
    "ARCHIVO_INVALIDO": "No se pudo leer el archivo como CSV de texto UTF-8.",
    "CABECERA_INVALIDA": "La cabecera debe ser exactamente: correo,nombre,rol",
    "ARCHIVO_VACIO": "El archivo no tiene filas de datos para procesar.",
    "ARCHIVO_MUY_GRANDE": "El archivo supera el tamaño máximo permitido de 1 MB.",
    "DEMASIADAS_FILAS": f"El archivo supera el máximo de {MAX_DATA_ROWS} filas de datos.",
}


def _file_error(code: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": code, "message": _FILE_ERRORS[code]},
    )


def _decode(raw_bytes: bytes) -> str:
    if len(raw_bytes) > MAX_FILE_SIZE_BYTES:
        raise _file_error("ARCHIVO_MUY_GRANDE")
    if len(raw_bytes) == 0:
        raise _file_error("ARCHIVO_VACIO")
    try:
        # utf-8-sig quita el BOM si está presente; decodifica UTF-8 normal igual.
        return raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _file_error("ARCHIVO_INVALIDO")


def _detect_delimiter(first_line: str) -> str:
    return ";" if first_line.count(";") > first_line.count(",") else ","


def _split_line(line: str, delimiter: str) -> list[str]:
    try:
        cells = next(csv.reader([line], delimiter=delimiter))
    except (csv.Error, StopIteration):
        cells = line.split(delimiter)
    return [c.strip() for c in cells]


def _parse_data_rows(text: str) -> list[tuple[int, str, str, str]]:
    """Devuelve (line, email_crudo, nombre_crudo, rol_crudo) por cada fila de
    datos no vacía. Filas totalmente vacías (línea en blanco, o todas sus
    celdas vacías) se ignoran -- ni cuentan para el límite de 200 ni
    aparecen en el resultado. Levanta 422 por cabecera/archivo inválido."""
    lines = text.splitlines()
    if not lines or all(not line.strip() for line in lines):
        raise _file_error("ARCHIVO_VACIO")

    delimiter = _detect_delimiter(lines[0])
    header = [c.lower() for c in _split_line(lines[0], delimiter)]
    if header != list(REQUIRED_HEADER):
        raise _file_error("CABECERA_INVALIDA")

    data_rows: list[tuple[int, str, str, str]] = []
    for line_no, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = _split_line(line, delimiter)
        if all(c == "" for c in cells):
            continue
        email = cells[0] if len(cells) > 0 else ""
        full_name = cells[1] if len(cells) > 1 else ""
        role = cells[2] if len(cells) > 2 else ""
        data_rows.append((line_no, email, full_name, role))

    if not data_rows:
        raise _file_error("ARCHIVO_VACIO")
    if len(data_rows) > MAX_DATA_ROWS:
        raise _file_error("DEMASIADAS_FILAS")
    return data_rows


async def _build_rows(db: AsyncSession, data_rows: list[tuple[int, str, str, str]]) -> list[BulkUserRow]:
    normalized = [
        (line, email.strip().lower(), (full_name.strip() or None), role.strip().lower())
        for line, email, full_name, role in data_rows
    ]
    email_counts = Counter(email for _, email, _, _ in normalized if email)

    candidate_emails = {email for _, email, _, _ in normalized if email}
    existing_emails: set[str] = set()
    if candidate_emails:
        result = await db.execute(
            select(func.lower(User.email)).where(func.lower(User.email).in_(candidate_emails))
        )
        existing_emails = {row[0] for row in result.all()}

    rows: list[BulkUserRow] = []
    for line, email, full_name, role in normalized:
        errors: list[BulkRowError] = []

        try:
            validate_email(email, check_deliverability=False)
        except EmailNotValidError:
            errors.append(BulkRowError(code="EMAIL_INVALIDO", message="El correo no tiene un formato válido."))
        else:
            if email_counts[email] > 1:
                errors.append(BulkRowError(
                    code="EMAIL_DUPLICADO_EN_ARCHIVO",
                    message="Este correo aparece más de una vez en el archivo.",
                ))
            elif email in existing_emails:
                errors.append(BulkRowError(
                    code="EMAIL_YA_REGISTRADO",
                    message="Ya existe una cuenta registrada con este correo.",
                ))

        if not role:
            errors.append(BulkRowError(code="ROL_VACIO", message="El rol es obligatorio."))
        elif role != BULK_ALLOWED_ROLE:
            errors.append(BulkRowError(
                code="ROL_NO_PERMITIDO",
                message='Solo se puede crear el rol "analista" por esta vía.',
            ))

        rows.append(BulkUserRow(
            line=line,
            email=email or None,
            full_name=full_name,
            role=role or None,
            status="ok" if not errors else "error",
            errors=errors,
        ))
    return rows


async def validate_bulk_upload(db: AsyncSession, raw_bytes: bytes) -> BulkValidationResponse:
    text = _decode(raw_bytes)
    data_rows = _parse_data_rows(text)
    rows = await _build_rows(db, data_rows)
    return BulkValidationResponse(
        valid=all(row.status == "ok" for row in rows),
        total=len(rows),
        rows=rows,
    )


async def create_users_in_bulk(
    db: AsyncSession,
    admin_user_id: int,
    raw_bytes: bytes,
    send_invitation: InvitationSender,
) -> BulkConfirmResponse:
    """Revalida el archivo (mismas reglas que /validate). Si hay una sola
    fila con error, 409 sin crear nada. Si todo es válido, crea todas las
    cuentas y sus tokens de invitación en UNA transacción (todo o nada) y
    solo DESPUÉS intenta enviar los correos -- un envío fallido no deshace
    la cuenta, solo se reporta en invitations_failed."""
    validation = await validate_bulk_upload(db, raw_bytes)
    if not validation.valid:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=validation.model_dump())

    settings = Settings()
    pending_invitations: list[tuple[str, str, int]] = []  # (email, raw_token, line)

    try:
        for row in validation.rows:
            unusable_password = secrets.token_urlsafe(32)
            user = User(
                email=row.email,
                hashed_password=get_password_hash(unusable_password),
                full_name=row.full_name,
                role=BULK_ALLOWED_ROLE,
                is_active=True,
            )
            db.add(user)
            await db.flush()
            raw_token = await create_invitation_token(db, user)
            pending_invitations.append((row.email, raw_token, row.line))

        log_action(
            db, admin_user_id, "bulk_create_users",
            details={"created": len(pending_invitations), "emails": [e for e, _, _ in pending_invitations]},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "BULK_CREATE_FAILED",
                "message": "No se pudieron crear las cuentas. No se guardó ningún cambio.",
            },
        )

    invitations_sent = 0
    invitations_failed: list[BulkInvitationFailure] = []
    for email, raw_token, line in pending_invitations:
        invite_url = f"{settings.frontend_base_url}/restablecer?token={raw_token}"
        try:
            await send_invitation(email, invite_url)
            invitations_sent += 1
        except Exception:
            invitations_failed.append(BulkInvitationFailure(email=email, line=line))

    return BulkConfirmResponse(
        created=len(pending_invitations),
        invitations_sent=invitations_sent,
        invitations_failed=invitations_failed,
    )
