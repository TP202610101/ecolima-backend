from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.dependencies import require_role
from app.db.session import get_session
from app.models.user import User
from app.services.alert_service import check_coverage_redundancy, get_active_alerts

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


@router.post("/check-coverage-redundancy")
async def alerts_check_coverage_redundancy(
    _: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    """admin — verifica redundancia de cobertura (qué % de zonas recomendadas
    por el modelo ya tienen un punto real cerca -- NO es llenado de
    contenedores, ver geo_service.get_coverage_redundancy_data) y registra
    alertas si algún distrito supera 80%. Es un paso MANUAL del panel admin
    -- no se dispara solo tras run-inference, hay que llamarlo aparte.
    Envía email si SMTP configurado."""
    smtp = _smtp_settings()
    admin_email = _settings.admin_email if smtp else None
    new_alerts = await check_coverage_redundancy(db, admin_email=admin_email, smtp_settings=smtp)
    return {
        "new_alerts": len(new_alerts),
        "districts_flagged": [a["district_name"] for a in new_alerts],
    }


@router.get("")
async def alerts_list(
    alert_status: str = Query(default="active", alias="status"),
    _: User = Depends(require_role("admin", "analista")),
    db: AsyncSession = Depends(get_session),
):
    """admin, analista — alertas para el banner del dashboard. Hoy solo se
    soporta ?status=active (no hay listado histórico implementado)."""
    if alert_status != "active":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "UNSUPPORTED_STATUS",
                "message": "Solo se soporta status=active",
                "allowed": ["active"],
            },
        )
    return await get_active_alerts(db)
