import asyncio
import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Awaitable, Callable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User

logger = logging.getLogger(__name__)

TOKEN_TTL_MINUTES = 10

# Mismos valores que MAX_LOGIN_ATTEMPTS/LOCKOUT_MINUTES en auth.py (no se
# importan desde ahí a propósito -- un service no debe depender del módulo de
# endpoints). Límite de solicitudes de recuperación por usuario: máximo
# MAX_RESET_REQUESTS dentro de una ventana de RESET_WINDOW_MINUTES.
MAX_RESET_REQUESTS = 5
RESET_WINDOW_MINUTES = 15

EmailSender = Callable[[str, str], Awaitable[None]]


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def create_reset_token(db: AsyncSession, user: User) -> str | None:
    """Crea un token de recuperación para `user` y devuelve el valor en claro
    (para el correo -- nunca se persiste). Devuelve None si el usuario superó
    el límite de solicitudes recientes; en ese caso el llamador no debe
    enviar correo, pero sí debe responder igual que si todo hubiera ido bien
    (la neutralidad de la respuesta no depende de esto).

    Cualquier solicitud válida invalida los tokens sin usar anteriores de
    `user` -- a lo sumo un token activo por usuario a la vez.
    """
    window_start = datetime.utcnow() - timedelta(minutes=RESET_WINDOW_MINUTES)
    recent_count = (await db.execute(
        select(func.count(PasswordResetToken.token_id)).where(
            PasswordResetToken.user_id == user.user_id,
            PasswordResetToken.created_at >= window_start,
        )
    )).scalar_one()

    if recent_count >= MAX_RESET_REQUESTS:
        return None

    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.user_id, PasswordResetToken.used_at.is_(None))
        .values(used_at=datetime.utcnow())
    )

    raw_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.user_id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.utcnow() + timedelta(minutes=TOKEN_TTL_MINUTES),
        created_at=datetime.utcnow(),
    ))
    await db.commit()
    return raw_token


async def get_valid_reset_token(db: AsyncSession, raw_token: str) -> PasswordResetToken | None:
    """Busca el token por su hash y devuelve la fila solo si existe, no fue
    usado y no venció. No lo marca como usado -- eso lo hace el llamador
    recién tras aplicar el cambio de contraseña con éxito, para no quemar un
    token válido si la nueva contraseña no cumple la política."""
    row = (await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(raw_token))
    )).scalar_one_or_none()

    if row is None or row.used_at is not None or row.expires_at < datetime.utcnow():
        return None
    return row


def _smtp_config(settings: Settings) -> dict | None:
    if all([settings.smtp_host, settings.smtp_port, settings.smtp_user, settings.smtp_pass]):
        return {
            "host": settings.smtp_host,
            "port": settings.smtp_port,
            "user": settings.smtp_user,
            "pass": settings.smtp_pass,
        }
    return None


async def _send_reset_email_smtp(to_email: str, reset_url: str) -> None:
    """Implementación real -- usada por el endpoint vía get_password_reset_sender().
    Si SMTP no está configurado, no falla: solo loguea un aviso y no envía
    nada (mismo comportamiento que alert_service para las alertas de
    redundancia). El token y la URL completa nunca se escriben en el log."""
    settings = Settings()
    smtp = _smtp_config(settings)
    if smtp is None:
        logger.warning(
            "SMTP no configurado -- no se envió el correo de recuperación de contraseña "
            "(el token igual quedó creado, con su vigencia de %d minutos).",
            TOKEN_TTL_MINUTES,
        )
        return

    text_body = (
        "Recibimos una solicitud para restablecer tu contraseña en EcoLima.\n\n"
        f"Para continuar, abre este enlace (válido por {TOKEN_TTL_MINUTES} minutos):\n"
        f"{reset_url}\n\n"
        "Si no lo solicitaste, ignora este correo -- tu contraseña no cambiará."
    )
    html_body = (
        "<p>Recibimos una solicitud para restablecer tu contraseña en EcoLima.</p>"
        f"<p>Para continuar, abre este enlace (válido por {TOKEN_TTL_MINUTES} minutos):</p>"
        f'<p><a href="{reset_url}">{reset_url}</a></p>'
        "<p>Si no lo solicitaste, ignora este correo -- tu contraseña no cambiará.</p>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "[EcoLima] Recuperación de contraseña"
    msg["From"] = smtp.get("user", "ecolima@noreply.com")
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    def _send_sync() -> None:
        try:
            with smtplib.SMTP(smtp["host"], int(smtp["port"])) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp["user"], smtp["pass"])
                server.send_message(msg)
            logger.info("Correo de recuperación de contraseña enviado a %s", to_email)
        except Exception as exc:
            logger.warning("No se pudo enviar el correo de recuperación de contraseña: %s", exc)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_sync)


def get_password_reset_sender() -> EmailSender:
    """Dependencia de FastAPI -- se sobreescribe en tests con
    app.dependency_overrides para no depender de un servidor SMTP real,
    igual que get_ml_api_client en ml_api_client.py."""
    return _send_reset_email_smtp
