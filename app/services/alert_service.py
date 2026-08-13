import asyncio
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.services.geo_service import get_coverage_redundancy_data

logger = logging.getLogger(__name__)


async def check_coverage_redundancy(
    db: AsyncSession,
    admin_email: str | None = None,
    smtp_settings: dict | None = None,
) -> list[dict]:
    """
    Verifica redundancia de cobertura por distrito (ver
    geo_service.get_coverage_redundancy_data para la definición exacta -- NO
    es llenado de contenedores) y crea alertas para los que superen 80%.
    Evita duplicados: no crea alerta si ya existe una activa para ese distrito.
    Envía email al admin si SMTP está configurado.
    """
    redundancy = await get_coverage_redundancy_data(db)
    new_alerts: list[dict] = []

    for district in redundancy:
        if district["redundancy_pct"] <= 80:
            continue

        existing = (await db.execute(
            select(Alert).where(
                Alert.district_id == district["district_id"],
                Alert.status == "active",
            )
        )).scalar_one_or_none()

        if existing is None:
            alert = Alert(
                district_id=district["district_id"],
                redundancy_pct=district["redundancy_pct"],
                status="active",
                created_at=datetime.utcnow(),
            )
            db.add(alert)
            new_alerts.append(district)

    if new_alerts:
        await db.commit()
        if admin_email and smtp_settings:
            await _send_coverage_redundancy_email(admin_email, smtp_settings, new_alerts)
        logger.info("Coverage redundancy alerts created for %d district(s)", len(new_alerts))

    return new_alerts


async def get_active_alerts(db: AsyncSession) -> list[dict]:
    """Devuelve alertas activas (no resueltas) con nombre de distrito."""
    rows = (await db.execute(text("""
        SELECT
            a.alert_id,
            a.district_id,
            d.district_name,
            a.redundancy_pct,
            a.status,
            a.created_at
        FROM alerts a
        JOIN districts d ON d.district_id = a.district_id
        WHERE a.status = 'active'
        ORDER BY a.created_at DESC
    """))).fetchall()

    return [
        {
            "alert_id": row[0],
            "district_id": row[1],
            "district_name": row[2],
            "redundancy_pct": float(row[3]),
            "status": row[4],
            "created_at": row[5].isoformat() if row[5] else None,
        }
        for row in rows
    ]


async def _send_coverage_redundancy_email(
    admin_email: str,
    smtp_settings: dict,
    districts: list[dict],
) -> None:
    lines = ["Alerta de redundancia de cobertura en Lima (recomendaciones ML donde ya hay un punto real cerca):\n"]
    for d in districts:
        lines.append(f"  - {d['district_name']}: {d['redundancy_pct']:.1f}% redundancia (rojo)")
    lines.append("\nRevisa el dashboard EcoLima para más detalles.")

    msg = MIMEText("\n".join(lines), "plain", "utf-8")
    msg["Subject"] = "[EcoLima] Alerta: redundancia de cobertura >80% detectada"
    msg["From"] = smtp_settings.get("user", "ecolima@noreply.com")
    msg["To"] = admin_email

    def _send_sync() -> None:
        try:
            with smtplib.SMTP(smtp_settings["host"], int(smtp_settings["port"])) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(smtp_settings["user"], smtp_settings["pass"])
                smtp.send_message(msg)
            logger.info("Coverage redundancy alert email sent to %s", admin_email)
        except Exception as exc:
            logger.warning("Could not send coverage redundancy alert email: %s", exc)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_sync)
