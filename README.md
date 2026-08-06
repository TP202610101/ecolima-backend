# ecolima-backend

Backend FastAPI de **EcoLima ML** — recomienda ubicaciones óptimas para nuevos
puntos de reciclaje en Lima Metropolitana combinando datos geoespaciales
(PostGIS) con un modelo LightGBM entrenado por el pipeline
[`ecolima-ml`](../ecolima-ml). Proyecto de tesis, Ingeniería de Software —
UPC 2026 (Alexander Cantoral, backend/frontend · Nikole García, ML).

Stack: FastAPI (async) · SQLAlchemy 2.0 + asyncpg · PostgreSQL/PostGIS ·
Alembic · JWT (python-jose) + bcrypt · LightGBM + SHAP · Azure App
Service / Blob Storage.

---

## Quickstart local

```bash
# 1. Levantar PostgreSQL + PostGIS local (Docker)
docker-compose up -d

# 2. Entorno virtual
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# 3. Variables de entorno
copy .env.example .env          # Windows — o `cp` en macOS/Linux
# editar .env: SECRET_KEY (mínimo 32 caracteres), ADMIN_EMAIL, ADMIN_PASSWORD, etc.

# 4. Migraciones + seeds
make migrate                    # alembic upgrade head
make seed                       # seed_districts.py + seed_admin.py

# 5. Levantar el servidor
uvicorn main:app --reload --port 8000
```

Swagger UI: http://localhost:8000/docs · Health check: http://localhost:8000/health

## Tests

```bash
make test          # equivale a: pytest tests/ -v
```

Los tests son de integración sobre HTTP (`httpx.AsyncClient` + ASGI
transport) y requieren `DATABASE_URL` apuntando a una Postgres/PostGIS
accesible con el usuario admin ya sembrado (`make seed`). No existe base de
datos de test separada todavía.

## Variables de entorno

Ver `.env.example` para la plantilla completa. Resumen:

| Variable | Obligatoria | Descripción |
|---|---|---|
| `DATABASE_URL` | Sí | `postgresql+asyncpg://user:pass@host/db` |
| `SECRET_KEY` | Sí | Firma JWT, mínimo 32 caracteres |
| `AZURE_BLOB_CONNECTION_STRING` | Sí* | Storage del modelo `.pkl`. Con el placeholder de ejemplo, cae al fallback local (`models/lightgbm_model.pkl`) |
| `AZURE_BLOB_CONTAINER_MODELS` | Sí* | Nombre del contenedor |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Sí | Usuario admin creado por `seed_admin.py` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No (30) | Expiración del JWT |
| `FRONTEND_URL` | No | Referencia informativa — **el CORS real está hardcodeado en `main.py`**, cambiar esta variable no tiene efecto |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | No | Alertas de saturación por email. Sin configurar, las alertas quedan solo en BD |
| `ML_INFERENCE_THRESHOLD` | No (0.5) | Umbral de clasificación binaria |
| `ML_PRIORITY_HIGH` / `ML_PRIORITY_MEDIUM` | No (0.7 / 0.4) | Umbrales de `priority_label` |

\* Declaradas como obligatorias en `config.py`, pero el código las maneja
defensivamente con fallback local si detecta el placeholder de ejemplo.

## Deploy en Azure — Notas

- El startup command usa 1 worker (`-w 1`) en lugar de 4 debido a memoria limitada del plan App Service. Las librerías ML (lightgbm, shap, geopandas, scipy) consumen suficiente RAM por proceso que 4 workers simultáneos causan errores SIGKILL out of memory.
- Si se escala a un plan con más RAM (B2 o superior), se puede aumentar el número de workers proporcionalmente.
- Startup command actual:
  ```
  gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000 --timeout 120
  ```
- Base de datos de pruebas: Neon (PostgreSQL serverless) con PostGIS activado.
- Variables de entorno requeridas en Azure: `DATABASE_URL`, `SECRET_KEY`, `AZURE_BLOB_CONNECTION_STRING`, `AZURE_BLOB_CONTAINER_MODELS`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `PYTHONPATH=/home/site/wwwroot`

## Demo deployeada: 
https://ecolima-backend-fbg6fzb4epd0eucr.canadacentral-01.azurewebsites.net/docs
