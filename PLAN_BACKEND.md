# EcoLima — Plan de Desarrollo del Backend
**Guía de referencia para IA · FastAPI + PostgreSQL/PostGIS + LightGBM + Azure**  
**Autores:** Alexander Cantoral · Nikole García — UPC 2026

> Este documento es la fuente de verdad del proyecto. Leerlo completo antes de generar cualquier código. Contiene el contexto, las decisiones técnicas ya tomadas, el estado del avance y las reglas de negocio que no pueden violarse.

---

## 1. Contexto del Proyecto

EcoLima es una aplicación web académica que usa **Machine Learning + GIS** para identificar ubicaciones óptimas de nuevos puntos de reciclaje en Lima Metropolitana, ayudando a la gestión municipal de residuos sólidos.

| Parámetro | Valor |
|-----------|-------|
| Alcance geográfico | 43 distritos de Lima Metropolitana |
| Celdas del grid | ~11,000 celdas de 500m × 500m |
| Datos reales disponibles | 53 puntos de reciclaje (Miraflores + Magdalena del Mar) |
| Modelo ML | LightGBM · target binario `is_suitable` · ~5% positivos |
| Métrica principal | **AUC-PR** (no AUC-ROC — dataset severamente desbalanceado) |
| Validación CV | **Spatial Block CV** (checkerboard — evita data leakage espacial) |

---

## 2. Arquitectura del Sistema

```
[Web Browser]
     |
     | HTTPS
     ↓
[Azure Static Web Apps]          [Azure App Service — Backend]
  Vue.js SPA          ←──REST──→  FastAPI Application
  (Alexander)                      (este repositorio)
                                         |
                          ┌──────────────┼──────────────┐
                          ↓              ↓              ↓
              [Azure Database      [Azure Blob      [Azure Key
               for PostgreSQL]      Storage]         Vault]
               PostGIS ext.         LightGBM .pkl    Secrets
                                    Artifacts
                                         |
                          ┌──────────────┘
                          ↓
              [Azure App Service — ML Training]
               Script Python de Nikole
               (entrena LightGBM, guarda .pkl en Blob)

[Azure Monitor + App Insights] — monitorea todo
```

---

## 3. Stack Tecnológico Definitivo

```
Backend:        FastAPI 0.115 (async)
ORM:            SQLAlchemy 2.0 async + asyncpg
Espacial:       GeoAlchemy2 + PostGIS 3.x
Migraciones:    Alembic
Auth:           JWT (python-jose) + bcrypt (passlib)
Validación:     Pydantic v2
ML inferencia:  LightGBM 4.5 + SHAP 0.46
Storage:        Azure Blob Storage (modelos .pkl)
Config:         pydantic-settings (Settings desde .env)
Testing:        pytest + httpx AsyncClient
Rate limiting:  slowapi
```

**Regla:** Todo el backend es async. Nunca usar `db.query()` (SQLAlchemy 1.x sync).

---

## 4. Estructura de Carpetas

```
ecolima-backend/
├── app/
│   ├── api/v1/endpoints/
│   │   ├── auth.py           # login, logout, register
│   │   ├── users.py          # CRUD usuarios (admin)
│   │   ├── datasets.py       # upload, validate, clean, export
│   │   ├── map.py            # GeoJSON endpoints para Leaflet
│   │   └── ml.py             # recommendations, inference, models
│   ├── core/
│   │   ├── config.py         # Settings (pydantic-settings)
│   │   ├── security.py       # JWT, bcrypt
│   │   └── dependencies.py   # get_current_user, require_role
│   ├── db/
│   │   ├── base.py           # Base declarativa SQLAlchemy
│   │   └── session.py        # AsyncSession factory
│   ├── models/               # 10 modelos SQLAlchemy
│   ├── schemas/              # Pydantic v2 schemas
│   └── services/
│       ├── auth_service.py
│       ├── dataset_service.py
│       ├── geo_service.py    # Todas las queries PostGIS
│       └── ml_service.py     # Inferencia LightGBM + SHAP
├── alembic/
│   ├── versions/001_initial.py
│   └── seeds/
│       ├── seed_districts.py  # 43 distritos Lima
│       └── seed_admin.py      # Usuario admin
├── tests/
├── main.py
├── requirements.txt
├── .env.example
├── docker-compose.yml
└── Makefile
```

---

## 5. Modelos de Base de Datos

### Diagrama de relaciones

```
districts (43 filas — estática)
    │
    ├──FK── socioeconomic_indicators
    ├──FK── waste_generation
    ├──FK── recycling_points ──────── fuente del target is_suitable
    ├──FK── candidate_zones  ──────── tabla central del sistema
    └──FK── alert

users
    ├──FK── dataset (uploaded_by)
    ├──FK── audit_log (user_id)
    ├──FK── model_version (trained_by)
    └──FK── alert (resolved_by)

dataset ──FK── audit_log
candidate_zones [Sección B → LightGBM → Sección C]
```

### Tabla candidate_zones — estructura de las 3 secciones

```
candidate_zones
├── Sección A — Geometría (siempre populada)
│   zone_id, centroid_lat, centroid_lon, geometry (Polygon), district_id, cell_area_km2
│
├── Sección B — Features ML (populadas al cargar el grid)
│   population_density, income_stratum, fuel_expenditure_sol, edu_level_head,
│   household_size_avg, pct_nse_ab, pct_nse_de, gpc_kg_per_capita_day,
│   pct_recyclable, pct_plastic, informal_recyclers_count, road_density,
│   dist_to_main_road_m, dist_to_market_m, dist_to_nearest_point_m,
│   existing_points_500m (NOT NULL DEFAULT 0), has_park_300m (NOT NULL DEFAULT false),
│   slope_pct, urbanized_area_pct, recycling_potential_index
│
├── Target (calculado via PostGIS tras commit de recycling_points)
│   is_suitable SMALLINT  -- 1 = hay punto real ≤200m / 0 = no / NULL = sin etiquetar
│
└── Sección C — Outputs ML (NULL hasta correr inferencia — NUNCA usar como features)
    ml_score, is_recommended, priority_label, recommendation_reason,
    coverage_gap_m, model_version, inference_date
```

### Reglas críticas de datos

- `geometry` siempre con **SRID 4326** (WGS84). Nunca insertar sin `ST_SetSRID`.
- `ST_Point(longitude, latitude)` — X = longitud, Y = latitud.
- Índice **GIST obligatorio** en todos los campos `geometry`. Sin él `ST_DWithin` es O(n²).
- `is_suitable = NULL` → celda sin etiquetar → **no entra al training set**.
- Columnas de Sección C **nunca entran como features** del modelo.
- `ml_score` solo visible para roles `cientifico` y `admin`.

---

## 6. Sistema de Roles y Permisos

| Rol | Descripción | Restricciones clave |
|-----|-------------|---------------------|
| `admin` | Administrador del sistema | Sin restricciones |
| `cientifico` | Científico de datos (Nikole) | Sin gestión de usuarios |
| `analista` | Analista Municipal | Solo lectura. Sin ml_score |
| `ciudadano` | Vista pública | Solo `/map/points/nearby` y `/map/points/{id}` |

**Visibilidad de ml_score:** Nunca exponer a `analista` ni `ciudadano`. Devolver solo `priority_label` (Alta/Media/Baja) y `recommendation_reason`.

---

## 7. Pipeline Completo de Datos → ML

```
1. UPLOAD        Analista/Científico sube CSV o XLSX (53 puntos reales)
      ↓
2. VALIDATE      FastAPI valida columnas obligatorias + tipos + rangos Lima
      ↓
3. CLEAN         Eliminar filas con NULL en lat/lon, editar valores erróneos
      ↓
4. COMMIT        Insertar en recycling_points con geometry PostGIS
      ↓
5. RECALCULATE   geo_service:
                 - calculate_is_suitable(threshold=200m) → etiqueta candidate_zones
                 - calculate_coverage_gaps()             → dist_to_nearest_point_m
                 - calculate_existing_points_500m()      → existing_points_500m
      ↓
6. EXPORT        GET /ml/training-set/export → CSV sin columnas Sección C
      ↓
7. TRAIN         Script de Nikole (externo al backend):
                 - LightGBM con scale_pos_weight = n_neg/n_pos
                 - Spatial Block CV (checkerboard)
                 - Métrica: AUC-PR
                 - Guarda .pkl en Azure Blob
                 - POST /ml/models/register para registrar la versión
      ↓
8. INFERENCE     POST /ml/run-inference → BackgroundTask:
                 - Carga .pkl desde Azure Blob
                 - predict_proba sobre todas las celdas con features completas
                 - Genera SHAP → recommendation_reason (texto plano)
                 - Escribe Sección C en candidate_zones
                 - check-saturation: alerta si >80% en algún distrito
      ↓
9. VISUALIZE     Vue.js consulta GET /ml/recommendations (GeoJSON)
                 Leaflet renderiza el mapa con capas y filtros
```

---

## 8. Endpoints REST — Referencia Completa

### Auth
```
POST   /api/v1/auth/login                      Público
POST   /api/v1/auth/logout                     Auth (todos)
POST   /api/v1/admin/users                     admin
PATCH  /api/v1/admin/users/{id}/role           admin
```

### Datasets
```
POST   /api/v1/datasets/upload                 admin, cientifico
GET    /api/v1/datasets                        admin, cientifico, analista
GET    /api/v1/datasets/{id}/validate          admin, cientifico
DELETE /api/v1/datasets/{id}/rows              admin, cientifico
DELETE /api/v1/datasets/{id}/rows/incomplete   admin, cientifico
PATCH  /api/v1/datasets/{id}/cells             admin, cientifico
POST   /api/v1/datasets/{id}/commit            admin, cientifico
GET    /api/v1/datasets/{id}/export            admin, cientifico, analista
GET    /api/v1/datasets/{id}/history           admin, cientifico
```

### Mapa (GeoJSON)
```
GET    /api/v1/map/points                      Auth (todos)
GET    /api/v1/map/points/{id}                 Auth (todos)
GET    /api/v1/map/points/filter               Auth (todos)
GET    /api/v1/map/points/nearby               Público (ciudadano)
GET    /api/v1/map/districts                   Auth (todos)
GET    /api/v1/map/districts/{id}/stats        admin, cientifico, analista
GET    /api/v1/map/comparison                  admin, cientifico, analista
GET    /api/v1/map/heatmap                     admin, cientifico, analista
GET    /api/v1/map/saturation                  admin, cientifico, analista
```

### ML
```
GET    /api/v1/ml/recommendations              Auth (todos — ml_score filtrado por rol)
POST   /api/v1/ml/run-inference                admin, cientifico
GET    /api/v1/ml/inference-status/{task_id}   admin, cientifico
GET    /api/v1/ml/training-set/export          admin, cientifico
GET    /api/v1/ml/training-set/stats           admin, cientifico
POST   /api/v1/ml/training-set/simulate        admin, cientifico
GET    /api/v1/ml/models                       admin, cientifico
GET    /api/v1/ml/models/{v}/metrics           admin, cientifico
POST   /api/v1/ml/models/{v}/activate          admin
POST   /api/v1/ml/models/register              admin, cientifico
```

### Sistema
```
GET    /health                                 Público
POST   /api/v1/geo/recalculate                 admin, cientifico
POST   /api/v1/alerts/check-saturation         admin, cientifico
GET    /api/v1/alerts/active                   admin, cientifico, analista
```

---

## 9. Queries PostGIS Críticas

### Calcular is_suitable (200m threshold)
```sql
UPDATE candidate_zones cz
SET is_suitable = CASE WHEN EXISTS (
    SELECT 1 FROM recycling_points rp
    WHERE ST_DWithin(rp.geometry::geography, cz.geometry::geography, 200)
) THEN 1 ELSE 0 END
WHERE cz.district_id IN (SELECT DISTINCT district_id FROM recycling_points)
```

### Insertar punto con geometry automática
```sql
INSERT INTO recycling_points (..., geometry)
VALUES (..., ST_SetSRID(ST_Point(:longitude, :latitude), 4326))
```

### Puntos cercanos por GPS
```sql
SELECT *, ST_Distance(geometry::geography,
    ST_SetSRID(ST_Point(:lon, :lat), 4326)::geography) AS distance_m
FROM recycling_points
WHERE ST_DWithin(geometry::geography,
    ST_SetSRID(ST_Point(:lon, :lat), 4326)::geography, :radius_m)
ORDER BY distance_m ASC
```

**Regla:** `::geography` siempre. Sin ello las distancias son en grados (inútil).

---

## 10. Features del Modelo LightGBM

### Orden exacto (CRÍTICO — el modelo espera este orden)

```python
FEATURE_COLUMNS = [
    "population_density",        # RF importance 0.48–0.51
    "income_stratum",            # R² = 0.647
    "fuel_expenditure_sol",      # FEATURE MÁS INFLUYENTE
    "edu_level_head",            # p = 0.002
    "household_size_avg",
    "pct_nse_ab",
    "pct_nse_de",
    "gpc_kg_per_capita_day",
    "pct_recyclable",
    "pct_plastic",               # dominante en RF/SVM
    "informal_recyclers_count",  # crítico contexto LAC
    "road_density",
    "dist_to_main_road_m",
    "dist_to_market_m",
    "dist_to_nearest_point_m",
    "existing_points_500m",
    "has_park_300m",
    "slope_pct",                 # CRÍTICO para laderas Lima Norte/Sur
    "urbanized_area_pct",
    "recycling_potential_index", # derivada = pop_density × gpc × pct_recyclable / 100
]
```

### Priority labels
```python
score >= 0.7  →  "Alta"
score >= 0.4  →  "Media"
score <  0.4  →  "Baja"
```

### Desbalance de clases
- ~5% positivos (is_suitable=1) — solo distritos con datos reales
- Usar `scale_pos_weight = n_negativos / n_positivos` en LightGBM
- Métrica: **AUC-PR**, no AUC-ROC

---

## 11. Historial de Usuario → Endpoint

| HU | Descripción | Endpoint | Bloque |
|----|-------------|----------|--------|
| HU-01 | Login con credenciales | POST /auth/login | 1 |
| HU-03 | Cierre de sesión | POST /auth/logout | 1 |
| HU-05 | Registro de usuarios | POST /admin/users | 1 |
| HU-06 | Asignar/modificar roles | PATCH /admin/users/{id}/role | 1 |
| HU-07 | Cargar dataset CSV/XLSX | POST /datasets/upload | 2 |
| HU-08 | Validar columnas clave | GET /datasets/{id}/validate | 2 |
| HU-09 | Validar tipos de datos | GET /datasets/{id}/validate | 2 |
| HU-10 | Eliminar filas vacías | DELETE /datasets/{id}/rows/incomplete | 2 |
| HU-11 | Editar valores | PATCH /datasets/{id}/cells | 2 |
| HU-12 | Eliminar filas erróneas | DELETE /datasets/{id}/rows | 2 |
| HU-13 | Ver dataset en tabla | GET /datasets/{id}/rows | 2 |
| HU-15 | Exportar dataset limpio | GET /datasets/{id}/export | 2 |
| HU-16 | Historial de datasets | GET /datasets/{id}/history | 2 |
| HU-19 | Cargar mapa base | GET /map/districts | 3 |
| HU-20 | Ver puntos en mapa | GET /map/points | 3 |
| HU-21 | Popup al hacer clic | GET /map/points/{id} | 3 |
| HU-22 | Filtrar por residuo | GET /map/points/filter | 3 |
| HU-23 | Filtrar por distrito | GET /map/points/filter | 3 |
| HU-25 | Comparar actual vs sugerido | GET /map/comparison | 3 |
| HU-27 | Heatmap zonas críticas | GET /map/heatmap | 3 |
| HU-29 | Puntos cercanos (GPS) | GET /map/points/nearby | 3 |
| HU-35 | Recomendaciones ML | GET /ml/recommendations | 3 |
| HU-36 | Importancia de variables | GET /ml/models/{v}/metrics | 4 |
| HU-38 | Predicción vs real | GET /ml/models/{v}/metrics | 4 |
| HU-40 | Métricas del modelo | GET /ml/models/{v}/metrics | 4 |
| HU-41 | Entrenar/correr modelo | POST /ml/run-inference | 4 |
| HU-42 | Guardar versiones | POST /ml/models/register | 4 |
| HU-43 | Comparar versiones | GET /ml/models/{v}/metrics | 4 |
| HU-44 | Alertas de saturación | POST /alerts/check-saturation | 4 |
| HU-45 | Semáforo de saturación | GET /map/saturation | 4 |

---

## 12. Estado de Avance

Actualizar este checklist conforme se van completando los prompts.

### Bloque 1 — Cimientos
- [ ] P-01: Estructura del proyecto
- [ ] P-02: Modelos SQLAlchemy (tablas de referencia)
- [ ] P-03: Modelos SQLAlchemy (tablas principales)
- [ ] P-04: Migraciones Alembic + seed distritos
- [ ] P-05: Auth JWT + endpoints
- [ ] **Verificación B1:** `GET /docs` muestra endpoints auth. Login con admin@ecolima.pe funciona.

### Bloque 2 — Ingesta
- [ ] P-06: Modelo Dataset + upload endpoint
- [ ] P-07: Validación de columnas y tipos
- [ ] P-08: Limpieza de datos + audit_log
- [ ] P-09: Commit a recycling_points + export
- [ ] **Verificación B2:** `SELECT COUNT(*) FROM recycling_points WHERE geometry IS NOT NULL` = 53

### Bloque 3 — API Geoespacial
- [ ] P-10: Endpoints GeoJSON puntos + distritos + nearby
- [ ] P-11: Filtros + comparación + heatmap
- [ ] P-12: Queries PostGIS para pipeline ML
- [ ] P-13: Export training set CSV
- [ ] **Verificación B3:** CSV descargado tiene ≥200 filas, columnas Sección B, sin Sección C

### Bloque 4 — ML
- [ ] P-14: Servicio de inferencia LightGBM
- [ ] P-15: SHAP explanations → texto plano
- [ ] P-16: Versionado de modelos + métricas
- [ ] P-17: Alertas + config final + tests
- [ ] **Verificación B4:** `pytest tests/` pasa. `GET /health` = `{ status: "ok", model_loaded: true }`

---

## 13. Convenciones de Código

- Nombres de archivos: `snake_case` siempre
- URLs de endpoints: `kebab-case`
- Schemas Pydantic: sufijo `Create` para input, `Response` para output
- Lógica de negocio: en `services/`, nunca inline en los endpoints
- Queries PostGIS complejas: en `geo_service.py`
- Errores: `HTTPException` con códigos semánticos (400, 401, 403, 404, 409, 422)
- Logging: módulo `logging` estándar, no `print()`

### Formato de errores

```python
raise HTTPException(
    status_code=422,
    detail={
        "code": "INVALID_COLUMNS",
        "message": "Faltan columnas obligatorias",
        "missing": ["latitude", "longitude"]
    }
)
```

### Patrón async obligatorio

```python
# ✅ Correcto
from sqlalchemy.ext.asyncio import AsyncSession
result = await db.execute(select(Model))

# ❌ Nunca usar
db.query(Model).all()
```

### GeoJSON siempre con ST_AsGeoJSON

```python
func.ST_AsGeoJSON(Model.geometry).label("geometry_json")
# Luego: json.loads(row["geometry_json"])
```

---

## 14. Checklist antes de cada commit

- [ ] Nuevos modelos con geometry tienen índice GIST
- [ ] Queries PostGIS usan `::geography` para cálculos en metros
- [ ] Nuevos endpoints tienen `Depends(require_role(...))` correcto
- [ ] Training set export excluye todas las columnas de Sección C
- [ ] Acciones de modificación registradas en `audit_log`
- [ ] `ml_score` filtrado por rol (no exponer a analista/ciudadano)
- [ ] Migración Alembic creada si se agregaron modelos nuevos

---

## 15. Variables de Entorno

```env
# Base de datos
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/ecolima

# Auth
SECRET_KEY=cambia-esto-por-32-chars-minimo-en-prod
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Azure
AZURE_BLOB_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
AZURE_BLOB_CONTAINER_MODELS=ecolima-models

# Frontend (CORS)
FRONTEND_URL=http://localhost:5173

# Email (alertas)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=ecolima@gmail.com
SMTP_PASS=app-password-aqui

# Seeds
ADMIN_EMAIL=admin@ecolima.pe
ADMIN_PASSWORD=AdminSeguro2026!

# ML
ML_INFERENCE_THRESHOLD=0.5
ML_PRIORITY_HIGH=0.7
ML_PRIORITY_MEDIUM=0.4
```

---

## 16. Comandos de desarrollo

```bash
# Setup inicial
docker-compose up -d          # PostgreSQL + PostGIS local
make migrate                   # alembic upgrade head
make seed                      # seed_districts + seed_admin

# Desarrollo
uvicorn app.main:app --reload  # servidor en localhost:8000
# Swagger UI: http://localhost:8000/docs

# Testing
make test                      # pytest tests/ -v
pytest tests/test_auth.py -v   # solo auth

# Migraciones
alembic revision --autogenerate -m "descripcion"  # nueva migración
alembic upgrade head           # aplicar
alembic downgrade -1           # revertir última

# Base de datos
psql -U postgres -d ecolima -c "SELECT COUNT(*) FROM recycling_points WHERE geometry IS NOT NULL;"
psql -U postgres -d ecolima -c "SELECT COUNT(*) FROM candidate_zones WHERE is_suitable IS NOT NULL;"
```
