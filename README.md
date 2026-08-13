# ecolima-backend

Backend REST de EcoLima: recomienda ubicaciones para nuevos puntos de reciclaje
en Lima Metropolitana combinando datos geoespaciales (PostGIS) con un modelo
LightGBM. Este repositorio es únicamente la API — el pipeline de entrenamiento
del modelo vive en un repo aparte (`ecolima-ml`).

## Tabla de contenidos

- [Stack](#stack)
- [Requisitos](#requisitos)
- [Setup local](#setup-local)
- [Variables de entorno](#variables-de-entorno)
- [Estructura del código](#estructura-del-código)
- [API](#api)
- [Tests](#tests)
- [Migraciones](#migraciones)
- [Despliegue](#despliegue)

---

## Stack

- **FastAPI** (async) + **Uvicorn** — framework HTTP / ASGI.
- **SQLAlchemy 2.0** (async) + **asyncpg** — ORM y driver de Postgres.
- **PostgreSQL + PostGIS** — Neon (serverless) en producción, Docker local en desarrollo.
- **Alembic** — migraciones de esquema.
- **JWT** (`python-jose`) + **bcrypt** (`passlib`) — autenticación.
- **LightGBM** + **SHAP** — inferencia del modelo y explicabilidad.
- **Gunicorn** + **UvicornWorker** — servidor de producción.
- **Azure App Service** — hosting; **Azure Blob Storage** — almacenamiento del modelo `.pkl` y de los datasets subidos (con fallback a disco local si Blob no está configurado).
- **slowapi** — rate limiting.
- **pytest** + `httpx.AsyncClient` — tests de integración sobre HTTP.

## Requisitos

- **Python 3.11** (versión fijada en el pipeline de CI/CD; el código no está probado contra otras versiones).
- **PostgreSQL con la extensión PostGIS** — local vía Docker (incluido, ver abajo) o una instancia de Neon.
- **Docker** (opcional, solo si se usa Postgres local en vez de Neon).
- Sin dependencias de sistema adicionales para levantar la API. `lightgbm`/`shap` se instalan como wheels precompilados en la mayoría de plataformas.

## Setup local

```bash
# 1. Clonar
git clone https://github.com/TP202610101/ecolima-backend.git
cd ecolima-backend

# 2. Entorno virtual
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# 3. Dependencias
pip install -r requirements.txt

# 4. Variables de entorno
cp .env.example .env            # o `copy` en Windows cmd
# Editar .env -- ver la tabla de abajo. Como mínimo: DATABASE_URL, SECRET_KEY
# (32+ caracteres), AZURE_BLOB_CONNECTION_STRING (dejar el placeholder de
# ejemplo cae a disco local, no rompe nada), ADMIN_EMAIL, ADMIN_PASSWORD.

# 5. Postgres + PostGIS local (si no se apunta a Neon)
docker compose up -d
# Publica el contenedor en localhost:5433 (no 5432 -- ver docker-compose.yml).
# DATABASE_URL en .env debe usar ese puerto:
#   postgresql+asyncpg://postgres:password@localhost:5433/ecolima

# 6. Migraciones + datos semilla
make migrate                    # alembic upgrade head
make seed                       # seed_districts.py + seed_admin.py (usa ADMIN_EMAIL/ADMIN_PASSWORD de .env)

# 7. Arrancar en desarrollo
uvicorn main:app --reload --port 8000
```

Swagger UI: `http://localhost:8000/docs` · Health check: `http://localhost:8000/health`

Arranque de **producción** (el que corre en Azure, ver [Despliegue](#despliegue)):

```bash
gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000 --timeout 120
```

(no funciona en Windows — `gunicorn` es Unix-only; en desarrollo local se usa `uvicorn` directo)

## Variables de entorno

Plantilla completa en `.env.example`. Los valores mostrados abajo son
placeholders — **nunca pegar secretos reales en este archivo ni en el repo**.

| Variable | Obligatoria | Descripción |
|---|---|---|
| `DATABASE_URL` | Sí | `postgresql+asyncpg://user:pass@host/db` |
| `SECRET_KEY` | Sí | Firma de JWT, mínimo 32 caracteres (validado en el arranque) |
| `AZURE_BLOB_CONNECTION_STRING` | Sí* | Connection string de Blob Storage. Con el placeholder de ejemplo (`AccountName=...`), el código cae a disco local (modelo: `models/lightgbm_model.pkl`; datasets: `uploads/datasets/`) |
| `AZURE_BLOB_CONTAINER_MODELS` | Sí* | Contenedor donde vive el modelo `.pkl` |
| `AZURE_BLOB_CONTAINER_DATASETS` | No (`datasets`) | Contenedor donde se guardan los datasets CSV/XLSX subidos |
| `CORS_ALLOWED_ORIGINS` | No | Orígenes CORS permitidos, separados por coma, sin barra final. Si no se setea, se usa una lista default hardcodeada en `config.py` |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Sí | Usuario admin creado por `seed_admin.py`. La contraseña debe cumplir la política: 8+ caracteres, mayúscula, minúscula y número |
| `ANALISTA_PASSWORD` | No | Solo usada por `scripts/create_analista.py` (cuenta de prueba local) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No (`30`) | Minutos de validez del JWT |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | No | Envío de alertas de redundancia de cobertura por email. Sin configurar, las alertas quedan solo en BD (no falla, se omite el envío) |
| `ML_INFERENCE_THRESHOLD` | No (`0.5`) | Umbral de clasificación binaria del modelo |
| `ML_PRIORITY_HIGH` / `ML_PRIORITY_MEDIUM` | No (`0.7` / `0.4`) | Umbrales de `priority_label` |
| `ML_API_URL` | No (`http://localhost:8001`) | URL del servicio externo `ecolima-ml`, para cuando la inferencia remota esté activa |
| `ML_API_TIMEOUT_SECONDS` | No (`15`) | Timeout de las llamadas a `ecolima-ml` |
| `ML_USE_REMOTE_API` | No (`false`) | Existe en `Settings` pero hoy no se lee en ningún branch real del código (solo aparece en un comentario `TODO` en `ml_service.py`) -- la inferencia siempre usa el modelo local `.pkl`, este valor no tiene efecto todavía |

\* Declaradas sin default en `config.py` (pydantic las exige), pero el código
las trata defensivamente: si `AZURE_BLOB_CONNECTION_STRING` contiene el
placeholder de ejemplo, no lanza error, cae al fallback local.

## Estructura del código

```
app/
├── api/v1/endpoints/  # Routers HTTP -- uno por dominio (auth, users, datasets,
│                      # map, ml, ml_service, geo, alerts, public). Delgados:
│                      # validan input y delegan a services/.
├── core/              # Settings (env vars), JWT/bcrypt/política de contraseña,
│                      # dependencias de auth (get_current_user, require_role),
│                      # rate limiter.
├── db/                # Engine y sesión async de SQLAlchemy (session.py, base.py).
├── models/            # Modelos ORM, un archivo por tabla.
├── schemas/           # Pydantic request/response (no todos los dominios
│                      # tienen schema propio -- algunos endpoints devuelven
│                      # dict directo).
└── services/          # Lógica de negocio -- una función por caso de uso.

alembic/
├── versions/          # Migraciones (7 al momento de escribir esto).
└── seeds/             # Scripts de siembra: distritos, admin, datos demo.

scripts/                # Utilitarios operativos sueltos (reset_admin,
                         # create_analista, cleanup_inference_tasks).

tests/                   # pytest -- ver sección Tests.
```

## API

Documentación interactiva (Swagger, generada automáticamente por FastAPI):
**`GET /docs`**. Ahí está la fuente de verdad de cada ruta, sus parámetros y
la forma exacta de cada respuesta — no se reproduce endpoint por endpoint
acá.

Dominios de alto nivel (prefijo `/api/v1/...` salvo `/health`, que es raíz):

| Prefijo | Dominio |
|---|---|
| `/auth` | Login, logout |
| `/admin/users` | Gestión de usuarios (crear, cambiar rol, activar/desactivar) — rol `admin` |
| `/datasets` | Carga, validación y confirmación de datasets CSV/XLSX de puntos de reciclaje |
| `/map` | Consultas GeoJSON para el mapa (puntos, distritos, heatmap, redundancia de cobertura) |
| `/ml` | Recomendaciones, ejecución de inferencia, gestión de versiones del modelo |
| `/ml/service` | Proxy hacia el servicio externo `ecolima-ml` (preparado, no activado por defecto) |
| `/geo` | Recálculo de campos derivados en PostGIS (`candidate_zones`) |
| `/alerts` | Alertas de redundancia de cobertura por distrito |
| `/public` | Único acceso sin autenticación: puntos de reciclaje cercanos (rate-limited) |
| `/health` | Health check público (estado de BD y del modelo cargado) |

Todos los endpoints salvo `/public/*` y `/health` requieren JWT (`Authorization: Bearer <token>`).

## Tests

```bash
make test          # equivale a: pytest (sin flags -- pytest.ini fija testpaths=tests)
```

17 archivos, 120 tests (118 pasan, 2 se saltan por requerir el servicio
externo `ecolima-ml` levantado). Corren como tests de integración reales
sobre HTTP (`httpx.AsyncClient` + ASGI transport), contra una base de datos
Postgres real accesible vía `DATABASE_URL` — no hay mocks de base de datos.
Requieren el usuario admin ya sembrado (`make seed`). No existe una base de
datos de test separada; los tests que crean datos limpian lo que insertan al
terminar (fixtures `cleanup_datasets`, `db_session`, etc. en `conftest.py`).

Cubren, a grandes rasgos: autenticación y hardening (rate limiting, timing,
JWT), flujo completo de datasets (upload → validate → commit, con las reglas
de integridad que lo protegen), reglas de seguridad de gestión de usuarios,
proxy hacia `ecolima-ml`, mapeo de features para el modelo, persistencia de
tareas de inferencia, y el endpoint público.

## Migraciones

Alembic gestiona el esquema. Comandos:

```bash
# Aplicar todas las migraciones pendientes
python -m alembic upgrade head        # o: make migrate

# Crear una migración nueva (vacía, para escribir a mano)
python -m alembic revision -m "descripción corta"

# Ver la revisión actual de la base de datos conectada
python -m alembic current

# Ver el historial completo
python -m alembic history
```

Las migraciones en este proyecto se escriben a mano (`op.create_table`,
`op.alter_column`, etc.), no se generan con `--autogenerate`. Antes de
aplicar una migración nueva contra una base de datos con datos reales,
probarla localmente con `upgrade` seguido de `downgrade` para confirmar que
es reversible.

## Despliegue

Azure App Service, desplegado por GitHub Actions en cada push a `develop`
(`.github/workflows/deploy-ecolimabackend-nuevo.yml`), autenticado por
publish profile (no OIDC/`azure/login`). El pipeline: `pip install -r
requirements.txt` → empaqueta → `azure/webapps-deploy@v3`.

Comando de arranque en producción (versionado en `Procfile` y `startup.txt`):

```
gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000 --timeout 120
```

Un solo worker (`-w 1`) a propósito: las librerías de ML (`lightgbm`, `shap`,
`geopandas`) consumen suficiente RAM por proceso que varios workers
simultáneos en el plan actual de App Service causan `SIGKILL` por memoria.
Escalar workers requiere primero escalar el plan.

Base de datos de producción: Neon (PostgreSQL serverless) con PostGIS
activado. Variables de entorno requeridas en Azure (Application Settings):
las mismas de la tabla de arriba marcadas como obligatorias, más
`PYTHONPATH=/home/site/wwwroot`.
