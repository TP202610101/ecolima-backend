from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.dependencies import require_role
from app.db.session import get_session
from app.models.user import User
from app.services.alert_service import check_saturation_alerts, get_active_alerts

router = APIRouter()

_settings = Settings()


def _smtp_settings() -> dict | None:
    if all([_settings.smtp_host, _settings.smtp_port, _settings.smtp_user, _settings.smtp_pass]):
        return {
            "host": _settings.smtp_host,
            "port": _settings.smtp_port,
            "user": _settings.smtp_user,
            "pass": _settings.smtp_pass,
        }
    return None


@router.post("/check-saturation")
async def alerts_check_saturation(
    _: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """admin — verifica saturación y registra alertas si algún distrito supera 80%.
    Llama automáticamente tras run-inference. Envía email si SMTP configurado."""
    smtp = _smtp_settings()
    admin_email = _settings.admin_email if smtp else None
    new_alerts = await check_saturation_alerts(db, admin_email=admin_email, smtp_settings=smtp)
    return {
        "new_alerts": len(new_alerts),
        "districts_flagged": [a["district_name"] for a in new_alerts],
    }


@router.get("/active")
async def alerts_get_active(
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — alertas activas para el banner del dashboard."""
    return await get_active_alerts(db)
