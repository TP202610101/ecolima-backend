import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import select, text

from app.api.v1.endpoints import alerts, auth, datasets
from app.api.v1.endpoints import map as map_
from app.api.v1.endpoints import ml, geo, users
from app.core.limiter import limiter
from app.db.session import AsyncSessionLocal
from app.models.model_version import ModelVersion
from app.services.ml_service import _model_cache

logger = logging.getLogger("ecolima.access")

app = FastAPI(title="EcoLima Backend")

# ── Rate limiting ──────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS ───────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vue.js dev server
        "http://localhost:8000",   # Swagger UI (/docs)
        "http://127.0.0.1:8000",   # Swagger UI via 127.0.0.1
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request logging middleware ─────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s %d %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(auth.router,     prefix="/api/v1/auth",         tags=["auth"])
app.include_router(users.router,    prefix="/api/v1/admin/users",  tags=["users"])
app.include_router(datasets.router, prefix="/api/v1/datasets",     tags=["datasets"])
app.include_router(map_.router,     prefix="/api/v1/map",          tags=["map"])
app.include_router(ml.router,       prefix="/api/v1/ml",           tags=["ml"])
app.include_router(geo.router,      prefix="/api/v1/geo",          tags=["geo"])
app.include_router(alerts.router,   prefix="/api/v1/alerts",       tags=["alerts"])


# ── Health check (requerido para Azure App Service) ────────────────────────────
@app.get("/health", tags=["system"])
async def health():
    """Público — estado del sistema para Azure App Service y monitoreo."""
    db_status = "error"
    active_version = "none"
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
            db_status = "connected"
            version_row = (await db.execute(
                select(ModelVersion.version_name).where(ModelVersion.is_active.is_(True))
            )).scalar_one_or_none()
            if version_row:
                active_version = version_row
    except Exception as exc:
        logger.warning("Health check DB error: %s", exc)

    return {
        "status": "ok",
        "db": db_status,
        "model_loaded": bool(_model_cache),
        "version": active_version,
    }
