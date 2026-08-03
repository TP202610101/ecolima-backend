"""
TEMPORAL -- endpoint de diagnóstico para SEC-AZURE-1.

Objetivo: verificar qué IP/headers ve el backend detrás del proxy de Azure
App Service, para confirmar si el rate limiter (slowapi, key_func=
get_remote_address -> request.client.host) ve la IP real del cliente o la
del balanceador interno de Azure, y si X-Forwarded-For / X-Real-IP llegan
de forma confiable. Ver auditoria-seguridad-backend.md, hallazgo I10
("NO VERIFICABLE sin Azure").

No expone nada sensible: solo refleja al cliente los mismos headers/IP que
él mismo mandó en su propia request -- ningún dato de otros usuarios ni del
servidor. Sin auth y sin rate limit a propósito, para que responda siempre
durante la medición.

ELIMINAR este archivo Y su registro en main.py (router `debug`) una vez
verificado SEC-AZURE-1. No es un endpoint permanente.
"""

from fastapi import APIRouter, Request

from app.core.limiter import limiter

router = APIRouter()


@router.get("/whoami")
@limiter.exempt
async def whoami(request: Request):
    """TEMPORAL (SEC-AZURE-1) -- diagnóstico de IP/headers tras el proxy de Azure."""
    forward_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower().startswith("x-forwarded") or key.lower().startswith("x-real")
    }
    return {
        "client_host": request.client.host if request.client else None,
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
        "x_real_ip": request.headers.get("x-real-ip"),
        "todos_los_headers_forward": forward_headers,
    }
