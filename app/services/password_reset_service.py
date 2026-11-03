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

# Vigencia de un token de invitación (creación de usuarios en bloque) --
# mismo mecanismo y tabla que la recuperación de contraseña (password_reset_tokens),
# solo que con una vigencia mayor y sin límite de solicitudes (lo inicia un
# admin, no el propio usuario). Si vence, la persona usa «¿olvidaste tu
# contraseña?» -- no hace falta un flujo de reenvío separado.
INVITATION_TTL_HOURS = 48

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


async def create_invitation_token(db: AsyncSession, user: User) -> str:
    """Como create_reset_token, pero para la invitación de creación en bloque:
    vigencia de INVITATION_TTL_HOURS (no TOKEN_TTL_MINUTES) y sin chequeo de
    límite de solicitudes -- lo dispara un admin, no el propio usuario, y
    cada cuenta nueva recibe exactamente un token. No hace `commit()`: queda
    a cargo del llamador (user_bulk_service), que crea cuentas y tokens en
    una sola transacción todo-o-nada."""
    raw_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.user_id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.utcnow() + timedelta(hours=INVITATION_TTL_HOURS),
        created_at=datetime.utcnow(),
    ))
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


def _build_message(smtp: dict, to_email: str, subject: str, text_body: str, html_body: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp.get("user", "ecolima@noreply.com")
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


async def _send_via_smtp(smtp: dict, msg: MIMEMultipart, to_email: str, log_label: str) -> None:
    """Envío real compartido por recuperar-contraseña e invitaciones. Deja
    que una excepción se propague -- cada llamador decide si eso debe
    silenciarse (recuperar-contraseña, por la respuesta neutra) o reportarse
    (invitaciones, que sí necesitan saber qué correo falló)."""
    def _send_sync() -> None:
        with smtplib.SMTP(smtp["host"], int(smtp["port"])) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp["user"], smtp["pass"])
            server.send_message(msg)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_sync)
    logger.info("%s enviado a %s", log_label, to_email)


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
    msg = _build_message(smtp, to_email, "[EcoLima] Recuperación de contraseña", text_body, html_body)

    try:
        await _send_via_smtp(smtp, msg, to_email, "Correo de recuperación de contraseña")
    except Exception as exc:
        logger.warning("No se pudo enviar el correo de recuperación de contraseña: %s", exc)


class SmtpNoConfigurado(RuntimeError):
    pass


async def _send_invitation_email_smtp(to_email: str, invite_url: str) -> None:
    """Implementación real -- usada por POST /admin/users/bulk/confirm vía
    get_invitation_sender(). A diferencia de _send_reset_email_smtp, SÍ deja
    que cualquier falla de envío se propague -- el llamador en
    user_bulk_service necesita saber qué correos fallaron para listarlos en
    invitations_failed, sin deshacer la cuenta ya creada.

    A diferencia de la recuperación de contraseña (donde "SMTP no
    configurado" se silencia porque la respuesta es neutral a propósito),
    acá SÍ importa que invitations_failed diga la verdad: si no se pudo
    avisarle a la persona, debe aparecer como fallo, no como "enviado". El
    token de invitación igual queda creado y válido por INVITATION_TTL_HOURS
    -- el admin puede reintentar el envío más adelante, o la persona puede
    pedir un enlace nuevo con «¿olvidaste tu contraseña?» una vez vencido.
    El enlace completo NUNCA se escribe en el log (es un secreto de un solo
    uso) -- ver el README para las formas soportadas de probar este flujo en
    local sin SMTP real (dependency_overrides, igual que los tests)."""
    settings = Settings()
    smtp = _smtp_config(settings)
    if smtp is None:
        logger.warning(
            "SMTP no configurado -- no se pudo enviar el correo de invitación "
            "(el token igual quedó creado, con su vigencia de %d horas).",
            INVITATION_TTL_HOURS,
        )
        raise SmtpNoConfigurado("SMTP no configurado")

    text_body = (
        "Se creó una cuenta para vos en EcoLima.\n\n"
        f"Para activarla, elegí tu contraseña en este enlace (válido por {INVITATION_TTL_HOURS} horas):\n"
        f"{invite_url}\n\n"
        "Si el enlace venció, podés pedir uno nuevo desde «¿Olvidaste tu contraseña?» en el login."
    )
    html_body = (
        "<p>Se creó una cuenta para vos en EcoLima.</p>"
        f"<p>Para activarla, elegí tu contraseña en este enlace (válido por {INVITATION_TTL_HOURS} horas):</p>"
        f'<p><a href="{invite_url}">{invite_url}</a></p>'
        "<p>Si el enlace venció, podés pedir uno nuevo desde «¿Olvidaste tu contraseña?» en el login.</p>"
    )
    msg = _build_message(smtp, to_email, "[EcoLima] Activa tu cuenta", text_body, html_body)
    await _send_via_smtp(smtp, msg, to_email, "Correo de invitación")


def get_password_reset_sender() -> EmailSender:
    """Dependencia de FastAPI -- se sobreescribe en tests con
    app.dependency_overrides para no depender de un servidor SMTP real,
    igual que get_ml_api_client en ml_api_client.py."""
    return _send_reset_email_smtp


def get_invitation_sender() -> EmailSender:
    """Como get_password_reset_sender, pero para la invitación de creación en
    bloque -- dependencia separada porque el comportamiento ante fallos de
    envío es distinto (ver _send_invitation_email_smtp): acá SÍ se propaga
    la excepción, para que el llamador registre el correo en
    invitations_failed sin deshacer la cuenta."""
    return _send_invitation_email_smtp
