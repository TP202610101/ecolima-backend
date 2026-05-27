# EcoLima — Guía de Prompts para Claude Code
**Stack:** FastAPI · PostgreSQL + PostGIS · LightGBM · Azure · Vue.js (frontend separado)  
**Autores:** Alexander Cantoral · Nikole García — UPC 2026

---

## Cómo usar esta guía

Cada prompt está diseñado para **una sola sesión de Claude Code**. Deben ejecutarse en el orden indicado: cada bloque genera archivos que el siguiente necesita como dependencia. Antes de empezar, ten la terminal activa en la raíz del repositorio.

> **Regla de oro:** Ejecuta siempre el comando de verificación al final de cada bloque antes de pasar al siguiente.

| Bloque | Tema | Prompts | Depende de |
|--------|------|---------|------------|
| Bloque 1 | Estructura + BD + Auth | P-01 → P-05 | — |
| Bloque 2 | Ingesta y validación de datasets | P-06 → P-09 | Bloque 1 |
| Bloque 3 | API geoespacial | P-10 → P-13 | Bloque 1 + 2 |
| Bloque 4 | Inferencia ML y trazabilidad | P-14 → P-17 | Bloque 1 + 2 + 3 |

---

## BLOQUE 1 — Cimientos del Proyecto
> Estructura · Modelos SQLAlchemy · Migraciones Alembic · JWT Auth

Sin este bloque ningún endpoint puede funcionar. Genera la estructura de carpetas, todos los modelos ORM del diccionario de datos, las migraciones y el sistema JWT.

---

### P-01 · Estructura del proyecto FastAPI

**Objetivo:** Scaffolding completo del backend con organización estándar para producción.

```
Crea la estructura completa de un proyecto FastAPI para una app llamada EcoLima. El backend usa:
- FastAPI + SQLAlchemy (async) + asyncpg
- PostgreSQL 15 con extensión PostGIS
- Alembic para migraciones
- JWT con python-jose + passlib
- Pydantic v2 para schemas

Estructura de carpetas requerida:
ecolima-backend/
  app/
    api/v1/endpoints/   (auth.py, datasets.py, map.py, ml.py, users.py)
    core/               (config.py, security.py, dependencies.py)
    db/                 (base.py, session.py)
    models/             (user.py, district.py, recycling_point.py, candidate_zone.py, dataset.py)
    schemas/            (todos los Pydantic v2 schemas)
    services/           (auth_service.py, dataset_service.py, geo_service.py, ml_service.py)
  alembic/
  tests/
  main.py
  requirements.txt
  .env.example
  docker-compose.yml (PostgreSQL + PostGIS)

Crea también:
- requirements.txt con todas las dependencias exactas
- .env.example con todas las variables (DATABASE_URL, SECRET_KEY, AZURE_BLOB_CONNECTION_STRING, etc.)
- main.py con el app FastAPI configurado con CORS para Vue.js en localhost:5173
```

**✅ Verificación:** Confirmar que existen `app/models/__init__.py` y `app/api/v1/__init__.py`. Revisar que `requirements.txt` incluya: `fastapi`, `sqlalchemy[asyncio]`, `asyncpg`, `geoalchemy2`, `alembic`, `python-jose`, `passlib`, `lightgbm`, `shap`, `geopandas`, `azure-storage-blob`.

---

### P-02 · Modelos SQLAlchemy — Tablas de referencia

**Objetivo:** Modelos ORM para `districts`, `socioeconomic_indicators` y `waste_generation`.

```
En app/models/, crea los modelos SQLAlchemy async para estas tres tablas del Data Dictionary de EcoLima:

1. districts (tabla de referencia estática — 43 distritos Lima Met.)
   Columnas: district_id (PK INT), district_name VARCHAR(100), geometry GEOMETRY(Polygon,4326) PostGIS,
   area_km2 FLOAT, province VARCHAR(100), region VARCHAR(100)
   Índice GIST obligatorio en geometry.

2. socioeconomic_indicators
   Columnas: soc_id SERIAL PK, district_id FK→districts, year SMALLINT,
   population_density FLOAT NOT NULL, total_population INT NOT NULL,
   income_stratum SMALLINT(1-5) NOT NULL, pct_nse_ab FLOAT NOT NULL,
   pct_nse_c FLOAT NOT NULL, pct_nse_de FLOAT NOT NULL,
   edu_level_head FLOAT NULLABLE, household_size_avg FLOAT NOT NULL,
   fuel_expenditure_sol FLOAT NULLABLE, pct_formal_employment FLOAT NULLABLE,
   pct_internet_access FLOAT NULLABLE, housing_type_apt_pct FLOAT NULLABLE,
   hacinamiento_idx FLOAT NULLABLE, pct_poverty FLOAT NULLABLE

3. waste_generation
   Columnas: waste_id SERIAL PK, district_id FK→districts, year SMALLINT NOT NULL,
   gpc_kg_per_capita_day FLOAT NOT NULL, pct_recyclable FLOAT NULLABLE,
   pct_organic FLOAT NULLABLE, pct_plastic FLOAT NULLABLE,
   pct_paper_cardboard FLOAT NULLABLE, pct_glass FLOAT NULLABLE,
   pct_metal FLOAT NULLABLE, collection_coverage_pct FLOAT NULLABLE,
   informal_recyclers_count INT NULLABLE, total_waste_tons_year FLOAT NULLABLE

Usa GeoAlchemy2 para el campo geometry. Agrega relaciones SQLAlchemy entre tablas.
Crea también app/db/base.py con la Base declarativa.
```

---

### P-03 · Modelos SQLAlchemy — Tablas principales

**Objetivo:** Modelos para `recycling_points`, `candidate_zones` (Secciones A/B/C) y `User`.

```
En app/models/, crea los modelos SQLAlchemy async para:

1. recycling_points (fuente del target is_suitable del modelo ML)
   Columnas: point_id SERIAL PK, district_id FK→districts,
   latitude FLOAT NOT NULL, longitude FLOAT NOT NULL,
   geometry GEOMETRY(Point,4326) NOT NULL (derivado de lat/lon),
   point_type VARCHAR(50) NULLABLE, address TEXT NULLABLE,
   operator VARCHAR(100) NULLABLE, materials_accepted TEXT NULLABLE,
   verified BOOLEAN NOT NULL DEFAULT false, source VARCHAR(50) NOT NULL,
   created_at TIMESTAMP DEFAULT NOW()
   Índice GIST en geometry.

2. candidate_zones (tabla central — ~11,000 filas, celdas 500m×500m)
   Sección A — Geometría:
     zone_id SERIAL PK, centroid_lat FLOAT NOT NULL, centroid_lon FLOAT NOT NULL,
     geometry GEOMETRY(Polygon,4326) NOT NULL, district_id FK→districts, cell_area_km2 FLOAT NOT NULL
   Sección B — Features ML (todas NULLABLE excepto existing_points_500m y has_park_300m):
     population_density, income_stratum SMALLINT, fuel_expenditure_sol, edu_level_head,
     household_size_avg, pct_nse_ab, pct_nse_de, gpc_kg_per_capita_day, pct_recyclable,
     pct_plastic, informal_recyclers_count INT, road_density, dist_to_main_road_m,
     dist_to_market_m, dist_to_nearest_point_m,
     existing_points_500m INT NOT NULL DEFAULT 0, has_park_300m BOOLEAN NOT NULL DEFAULT false,
     slope_pct, urbanized_area_pct, recycling_potential_index
   Target: is_suitable SMALLINT NULLABLE (1/0/NULL)
   Sección C — Outputs post-inferencia (todos NULLABLE):
     ml_score FLOAT, is_recommended BOOLEAN, priority_label VARCHAR(10),
     recommendation_reason TEXT, coverage_gap_m FLOAT,
     model_version VARCHAR(20), inference_date TIMESTAMP
   Índice GIST en geometry. Índices en district_id, priority_label, is_recommended.

3. User model:
   user_id SERIAL PK, email VARCHAR(255) UNIQUE NOT NULL, hashed_password VARCHAR NOT NULL,
   full_name VARCHAR(200), role VARCHAR(20) NOT NULL DEFAULT 'analista'
   (valores: admin, analista, cientifico, ciudadano),
   is_active BOOLEAN DEFAULT true, created_at TIMESTAMP DEFAULT NOW()
```

---

### P-04 · Migraciones Alembic + Seed de distritos

**Objetivo:** Configurar Alembic, migración inicial con todas las tablas y seed de los 43 distritos.

```
Configura Alembic para el proyecto EcoLima y crea la migración inicial:

1. Configura alembic.ini y alembic/env.py para usar la DATABASE_URL del .env
   e importar todos los modelos SQLAlchemy ya creados (necesario para autogenerate).

2. Crea la migración inicial que:
   - Ejecute CREATE EXTENSION IF NOT EXISTS postgis antes de crear tablas
   - Cree las tablas en orden correcto respetando FKs:
     districts → socioeconomic_indicators → waste_generation → recycling_points → candidate_zones
   - Agregue índices GIST en todos los campos geometry
   - Agregue índices regulares en: district_id (en todas las tablas),
     priority_label, is_recommended, is_suitable

3. Crea alembic/seeds/seed_districts.py con los 43 distritos de Lima Metropolitana
   (district_id = UBIGEO INEI, province = "Lima", region = "Lima Metropolitana"):
   Miraflores, San Isidro, Magdalena del Mar, San Miguel, Lince, Jesús María, Barranco,
   Surco, La Molina, San Borja, Surquillo, San Juan de Miraflores, Villa El Salvador,
   Villa María del Triunfo, Chorrillos, Ate, Santa Anita, El Agustino, San Juan de Lurigancho,
   Los Olivos, Comas, Carabayllo, Puente Piedra, Independencia, Rímac, Breña, La Victoria,
   Lima Cercado, Pueblo Libre, Magdalena Vieja, San Luis, Lurigancho, Chaclacayo, Cieneguilla,
   Pachacámac, Lurín, Punta Hermosa, Punta Negra, Santa María del Mar, San Bartolo,
   Pucusana, Ancón, Santa Rosa.

4. Agrega Makefile con: make migrate (alembic upgrade head), make seed, make test
```

---

### P-05 · Autenticación JWT + Endpoints Auth

**Objetivo:** Sistema JWT completo, login/logout y gestión de usuarios (HU-01, HU-03, HU-05, HU-06).

```
Implementa el sistema de autenticación JWT completo para EcoLima:

1. app/core/security.py:
   - create_access_token(data, expires_delta) → JWT con python-jose
   - verify_password / get_password_hash con bcrypt
   - Token expiry: 30 min (configurable en .env)

2. app/core/dependencies.py:
   - get_current_user(token) → User | HTTPException 401
   - require_role(*roles) → lanza 403 si el rol no coincide
     Ejemplo: require_role("admin") bloquea a analistas

3. POST /api/v1/auth/login
   Body: { email, password }
   Retorna: { access_token, token_type, user: {id, email, role} }
   Bloquea tras 5 intentos fallidos por email
   401 con mensaje "Credenciales incorrectas" si falla

4. POST /api/v1/auth/logout
   Invalida el token (blacklist en memoria o Redis)
   Retorna 200 { message: "Sesión cerrada" }

5. POST /api/v1/admin/users  (solo role=admin)
   Body: { email, password, full_name, role }
   409 si el email ya existe
   Retorna el usuario creado sin hashed_password

6. PATCH /api/v1/admin/users/{user_id}/role  (solo role=admin)
   Body: { role: "analista" | "cientifico" | "ciudadano" | "admin" }
   Retorna usuario actualizado

Schemas Pydantic v2 en app/schemas/user.py: UserCreate, UserResponse, TokenResponse, LoginRequest.
Seed: usuario admin por defecto (email: admin@ecolima.pe, password desde .env).
```

> **✅ Verificación Bloque 1:** Ejecutar `uvicorn app.main:app --reload` y abrir `/docs` en `localhost:8000`. Todos los endpoints de auth deben aparecer y funcionar con el usuario admin seed.

---

## BLOQUE 2 — Ingesta y Validación de Datasets
> Upload CSV/XLSX · Validación · Limpieza · Auditoría

Este bloque alimenta `recycling_points`, la tabla fuente del target `is_suitable`. Sin datos reales aquí no hay entrenamiento posible.

---

### P-06 · Modelo Dataset + Upload endpoint

```
Crea el modelo Dataset y el endpoint de upload en EcoLima:

1. Modelo app/models/dataset.py:
   dataset_id SERIAL PK, filename VARCHAR(255) NOT NULL,
   original_filename VARCHAR(255), uploaded_by FK→users,
   uploaded_at TIMESTAMP DEFAULT NOW(), file_size_bytes INT, row_count INT,
   status VARCHAR(20) DEFAULT 'pending' (pending/valid/invalid/processing/committed),
   detected_columns TEXT (JSON array), error_summary TEXT

2. POST /api/v1/datasets/upload  (rol: admin, cientifico)
   - Acepta UploadFile CSV o XLSX únicamente — 422 para otros formatos
   - Rechaza archivos > 20MB con mensaje claro
   - Lee con pandas (pd.read_csv o pd.read_excel según extensión)
   - Detecta columnas presentes → guarda en detected_columns
   - Persiste registro en datasets con status='pending'
   - Retorna: { dataset_id, filename, row_count, detected_columns, status }

3. GET /api/v1/datasets  (requiere auth)
   Paginación con skip/limit
   Retorna: [{ dataset_id, filename, uploaded_at, row_count, status }]

Schemas Pydantic v2 en app/schemas/dataset.py: DatasetResponse, DatasetListItem.
```

---

### P-07 · Validación de columnas y tipos de datos

```
Crea el servicio de validación en app/services/dataset_service.py:

1. GET /api/v1/datasets/{dataset_id}/validate  (requiere auth)
   Ejecuta dos validaciones:

   a) Validación de columnas (HU-08):
      Obligatorias: latitude, longitude, district_id, source
      Opcionales esperadas: point_type, address, operator, materials_accepted, verified
      Si faltan obligatorias → { valid: false, missing_columns: [...] }

   b) Validación de tipos (HU-09):
      latitude: float entre -13.0 y -11.5 (rango Lima)
      longitude: float entre -77.5 y -76.5 (rango Lima)
      verified: bool o convertible (true/false/1/0)
      Devuelve lista de { row_index, column, value, error }

2. Actualiza status del dataset: 'valid' si pasa, 'invalid' con error_summary si falla

3. Respuesta completa:
   { dataset_id, valid, missing_columns, type_errors: [{row, column, value, error}],
     row_count, valid_rows, error_rows }
```

---

### P-08 · Limpieza: eliminar filas + editar celdas

```
Implementa los endpoints de limpieza de datos (HU-10, HU-11, HU-12):

1. DELETE /api/v1/datasets/{dataset_id}/rows
   Body: { row_indices: [int], reason: str }
   Query param confirm=false → preview sin ejecutar
   Query param confirm=true → ejecuta y registra en audit_log
   Retorna: { deleted_count, remaining_rows }

2. PATCH /api/v1/datasets/{dataset_id}/cells
   Body: { edits: [{ row_index: int, column: str, new_value: any }] }
   Valida tipo correcto para cada columna
   Registra en audit_log: { user_id, dataset_id, action: "edit_cell",
     row_index, column, old_value, new_value, timestamp }
   Retorna: { edited_count, edits_applied: [...] }

3. DELETE /api/v1/datasets/{dataset_id}/rows/incomplete  (automático HU-10)
   Elimina filas donde latitude, longitude o district_id sean NULL
   Retorna: { deleted_count, remaining_rows }

Crea modelo app/models/audit_log.py:
audit_id SERIAL PK, user_id FK, action VARCHAR(50), dataset_id INT FK, details TEXT (JSON), created_at TIMESTAMP
```

---

### P-09 · Commit a recycling_points + Exportar dataset

```
Implementa commit y exportación de datasets (HU-15, HU-16):

1. POST /api/v1/datasets/{dataset_id}/commit  (solo admin/cientifico)
   - Inserta registros validados en recycling_points
   - geometry = ST_SetSRID(ST_Point(longitude, latitude), 4326)  [X=lon, Y=lat]
   - Duplicados: si ya existe punto con lat/lon ±0.0001 → skip con warning
   - Registra acción en audit_log
   - Retorna: { inserted: int, skipped_duplicates: int, errors: [] }

2. GET /api/v1/datasets/{dataset_id}/export
   Query param: format=csv|xlsx (default: csv)
   Nombre del archivo: {original_filename}_limpio_{YYYY-MM-DD}.csv
   Content-Disposition: attachment
   Añade columna exported_at

3. GET /api/v1/datasets/{dataset_id}/history
   Lee audit_log filtrando por dataset_id
   Retorna: { dataset_id, filename, uploads, validations, edits, commit, exported_at }

Agregar índice en recycling_points.geometry para acelerar ST_DWithin.
```

> **✅ Verificación Bloque 2:** Importar el Excel de Nikole (53 puntos, Miraflores + Magdalena del Mar): upload → validate → commit. Luego: `SELECT COUNT(*) FROM recycling_points WHERE geometry IS NOT NULL;` debe retornar 53.

---

## BLOQUE 3 — API Geoespacial
> GeoJSON · PostGIS · Filtros · Mapa para Leaflet en Vue.js

Expone los datos al frontend Vue.js vía GeoJSON compatible con Leaflet. Implementa también las queries PostGIS que calculan features para el modelo ML.

---

### P-10 · Endpoints de puntos de reciclaje (GeoJSON)

```
Crea los endpoints geoespaciales en app/api/v1/endpoints/map.py (HU-19, HU-20, HU-21):

1. GET /api/v1/map/points
   Query params: district_id?, material?, verified_only (default false)
   Retorna GeoJSON FeatureCollection usando ST_AsGeoJSON de PostGIS
   Properties: point_id, point_type, address, operator, materials_accepted, verified, source

2. GET /api/v1/map/points/{point_id}
   Feature completo con JOIN a districts (district_name)
   Para el popup de Leaflet (HU-21)

3. GET /api/v1/map/districts
   GeoJSON FeatureCollection con los 43 distritos
   Properties: district_id, district_name, area_km2
   Usa ST_AsGeoJSON(geometry) para el polígono

4. GET /api/v1/map/points/nearby
   Query params: lat, lon, radius_m (default 1000, max 5000)
   ST_DWithin con ::geography para metros exactos
   Ordena por ST_Distance ASC
   Incluye campo distance_m  (HU-29 vista ciudadano)

Encapsular queries PostGIS en app/services/geo_service.py.
Retornar siempre Content-Type: application/geo+json.
```

---

### P-11 · Filtros de mapa y comparación

```
Endpoints de filtros y comparación (HU-22, HU-23, HU-25):

1. GET /api/v1/map/points/filter
   Query params combinables: district_id?, material? (ILIKE), point_type?, verified?
   Retorna GeoJSON FeatureCollection filtrada

2. GET /api/v1/map/districts/{district_id}/stats
   Retorna: { district_name, total_points, verified_points,
              materials_breakdown: {plastic: n, paper: n, ...},
              coverage_pct, avg_distance_to_nearest_m }
   Usar CTEs en PostgreSQL (una sola query)

3. GET /api/v1/map/comparison?mode=current_vs_recommended
   Retorna:
   { current: FeatureCollection (recycling_points existentes),
     recommended: FeatureCollection (candidate_zones donde is_recommended=true) }
   Para el split-view 50/50 de Vue.js (HU-25)

4. GET /api/v1/map/heatmap
   Query params: district_id?, metric=density|priority|gap
   Retorna candidate_zones como GeoJSON con propiedad value para Leaflet.heat
```

---

### P-12 · Queries PostGIS para el pipeline ML

```
Crea el servicio de consultas PostGIS en app/services/geo_service.py:

1. calculate_is_suitable(threshold_m: int = 200)
   UPDATE candidate_zones cz
   SET is_suitable = CASE WHEN EXISTS (
     SELECT 1 FROM recycling_points rp
     WHERE ST_DWithin(rp.geometry::geography, cz.geometry::geography, :threshold_m)
   ) THEN 1 ELSE 0 END
   WHERE cz.district_id IN (SELECT DISTINCT district_id FROM recycling_points)
   Retorna: { updated_zones, positive_labels, negative_labels, null_zones }

2. calculate_coverage_gaps()
   UPDATE candidate_zones SET
     coverage_gap_m = (SELECT MIN(ST_Distance(rp.geometry::geography, cz.geometry::geography))
                       FROM recycling_points rp),
     dist_to_nearest_point_m = (misma subquery)

3. calculate_existing_points_500m()
   UPDATE existing_points_500m con conteo de puntos en buffer 500m por celda

4. POST /api/v1/geo/recalculate  (solo admin/cientifico)
   Ejecuta los tres métodos en secuencia
   Llamar después de cada commit de nuevos recycling_points

CRÍTICO: Siempre usar ::geography para que los umbrales sean en metros, no grados.
```

---

### P-13 · Exportar training set CSV

```
Endpoint de exportación del dataset de entrenamiento para LightGBM:

1. GET /api/v1/ml/training-set/export  (solo cientifico, admin)
   SELECT de candidate_zones: zone_id, centroid_lat, centroid_lon,
   todas las columnas Sección B, is_suitable
   WHERE is_suitable IS NOT NULL
   NUNCA incluir: columnas Sección C, geometry
   Nombre: grid_lima_train_{YYYY-MM-DD}.csv
   Content-Disposition: attachment

2. GET /api/v1/ml/training-set/stats
   Retorna: { total_labeled, positive_labels, negative_labels, null_labels,
              class_imbalance_ratio, features_available, districts_covered }

3. POST /api/v1/ml/training-set/simulate  (solo para hito académico)
   Genera 250 filas con distribuciones realistas para Lima (numpy):
   population_density: normal(6000, 3000) clip(500, 18000)
   income_stratum: randint(1, 6)
   fuel_expenditure_sol: normal(48, 18) clip(10, 120)
   is_suitable: binomial(1, 0.05)  — 5% positivos para simular desbalance real
   (usar distribuciones análogas para el resto de features según el diccionario de datos)
```

> **✅ Verificación Bloque 3:** Descargar el CSV de entrenamiento. Debe tener ≥200 filas etiquetadas, todas las columnas de Sección B y ninguna de Sección C.

---

## BLOQUE 4 — Inferencia ML y Trazabilidad
> LightGBM · SHAP · Versionado · Alertas · Azure Blob

Implementa el ciclo completo: carga del modelo desde Azure Blob, predicción sobre `candidate_zones`, SHAP explanations y escritura de la Sección C. También versionado y alertas de saturación.

---

### P-14 · Servicio de inferencia LightGBM

```
Crea app/services/ml_service.py con el servicio de inferencia:

1. load_model(version: str = "latest")
   Carga .pkl/.joblib desde Azure Blob (contenedor "ecolima-models")
   Usar azure-storage-blob con connection string del .env
   Cachear en memoria (no recargar en cada request)
   Fallback: buscar localmente en models/lightgbm_model.pkl

2. run_inference(model_version: str, threshold: float = 0.5)
   - Lee candidate_zones Sección B donde todas las features NOT NULL
   - Prepara DataFrame con FEATURE_COLUMNS en el orden exacto
   - Ejecuta model.predict_proba()[:, 1] → ml_score
   - is_recommended = ml_score > threshold
   - priority_label: Alta ≥0.7, Media 0.4–0.7, Baja <0.4
   - Genera recommendation_reason con SHAP (top-3 features → texto plano)
   - UPDATE masivo en Sección C de candidate_zones
   - Registra en model_versions

3. POST /api/v1/ml/run-inference  (solo admin/cientifico)  (HU-41)
   Body: { model_version?: str, threshold?: float }
   Ejecuta run_inference como FastAPI BackgroundTask
   Retorna inmediatamente: { task_id, status: "running", estimated_zones: int }

4. GET /api/v1/ml/inference-status/{task_id}
   Retorna: { status: "running"|"done"|"error", progress_pct, zones_processed }
```

---

### P-15 · SHAP explanations → texto plano

```
Implementa generate_explanation() en app/services/ml_service.py:

1. generate_explanation(shap_values_row, feature_names, feature_values) → str
   - Top-3 features por |shap_value|
   - Mapear nombre técnico → nombre legible:
     population_density          → "densidad poblacional"
     dist_to_nearest_point_m     → "distancia al punto más cercano"
     recycling_potential_index   → "índice de potencial reciclable"
     fuel_expenditure_sol        → "gasto promedio en combustible"
     gpc_kg_per_capita_day       → "generación de residuos per cápita"
     existing_points_500m        → "puntos existentes en 500m"
     (mapear todas las features de Sección B)
   - Plantilla: shap > 0 → "alta {label} ({value})", shap < 0 → "baja {label} ({value})"
   - Output ejemplo: "Zona recomendada por: alta densidad poblacional (8,540 hab/km²),
     baja cobertura actual (punto más cercano a 1.2 km) y alto potencial reciclable (índice 312)."

2. GET /api/v1/ml/recommendations  (HU-35)
   Query params: priority=Alta|Media|Baja|all, district_id?, limit (default 50)
   Retorna GeoJSON FeatureCollection donde is_recommended=true
   Properties: zone_id, priority_label, recommendation_reason, coverage_gap_m,
               centroid_lat, centroid_lon, district_name
   ml_score: SOLO si rol=cientifico o admin
   Ordenado por ml_score DESC
```

---

### P-16 · Versionado de modelos + métricas

```
Implementa versionado ML (HU-40, HU-42, HU-43):

1. Modelo app/models/model_version.py:
   version_id SERIAL PK, version_name VARCHAR(20) UNIQUE NOT NULL,
   training_date TIMESTAMP, trained_by FK→users,
   artifact_url VARCHAR(500), metrics TEXT (JSON: accuracy, auc_pr, f1, precision, recall, rmse),
   features_used TEXT (JSON array), is_active BOOLEAN DEFAULT false, created_at TIMESTAMP

2. GET /api/v1/ml/models  (HU-42)
   Retorna: [{ version_name, training_date, metrics, is_active, artifact_url }]

3. GET /api/v1/ml/models/{version}/metrics  (HU-40, HU-43)
   Métricas detalladas + tabla comparativa con versión anterior si existe

4. POST /api/v1/ml/models/{version}/activate  (solo admin)
   is_active=true en esta versión, false en las demás
   Actualiza el modelo cargado en memoria

5. POST /api/v1/ml/models/register
   Body: { version_name, artifact_url, metrics: {...} }
   Para que el script de entrenamiento de Nikole registre nuevas versiones
```

---

### P-17 · Alertas de saturación + configuración final

```
Sistema de alertas y configuración final (HU-44, HU-45):

1. GET /api/v1/map/saturation
   Por distrito: { district_id, district_name, total_recommended, already_covered,
                   saturation_pct, status }
   Semáforo: verde 0–50%, amarillo 51–80%, rojo >80%

2. POST /api/v1/alerts/check-saturation  (llamado automáticamente tras run-inference)
   Si saturation_pct > 80 en cualquier distrito:
   - Registra en tabla alerts
   - Envía email al admin via SMTP (configurado en .env)

3. GET /api/v1/alerts/active
   Retorna alertas no resueltas (para el banner rojo del dashboard)

4. Modelo app/models/alert.py:
   alert_id SERIAL PK, district_id FK, saturation_pct FLOAT,
   status "active"|"resolved", created_at TIMESTAMP,
   resolved_at TIMESTAMP NULLABLE, resolved_by FK→users NULLABLE

5. Configuración final en main.py:
   - CORS: origins=[FRONTEND_URL del .env], allow_credentials=True
   - Rate limiting: slowapi — 100 req/min en endpoints públicos, 20 req/min en /ml/
   - GET /health → { status: "ok", db: "connected", model_loaded: bool, version: str }
   - Middleware de logging de requests

6. Crea tests/test_auth.py y tests/test_datasets.py
   Mínimo 3 tests por módulo usando pytest + httpx AsyncClient
```

> **✅ Verificación Bloque 4:** Ejecutar `pytest tests/`. El backend está listo para conectar con Vue.js cuando `GET /health` retorne `{ status: "ok", db: "connected", model_loaded: true }`.

---

## Resumen de archivos generados

| Archivo | Contenido | Bloque |
|---------|-----------|--------|
| `app/models/*.py` (10 archivos) | User, District, SocioEcon, WasteGen, RecyclingPoint, CandidateZone, Dataset, AuditLog, ModelVersion, Alert | 1, 2, 4 |
| `app/core/security.py` | JWT, bcrypt, token blacklist | 1 |
| `app/core/dependencies.py` | get_current_user, require_role | 1 |
| `app/api/v1/endpoints/auth.py` | login, logout, register, role assignment | 1 |
| `app/api/v1/endpoints/datasets.py` | upload, validate, clean, export, commit | 2 |
| `app/api/v1/endpoints/map.py` | GeoJSON points, districts, nearby, filter, heatmap | 3 |
| `app/api/v1/endpoints/ml.py` | recommendations, inference, models, saturation, alerts | 3, 4 |
| `app/services/geo_service.py` | PostGIS queries: ST_DWithin, ST_Distance, coverage gaps | 3 |
| `app/services/ml_service.py` | LightGBM inference, SHAP → texto, Azure Blob | 4 |
| `app/services/dataset_service.py` | pandas validation, audit logging | 2 |
| `alembic/versions/001_initial.py` | Todas las tablas + índices GIST + PostGIS extension | 1 |
| `alembic/seeds/seed_districts.py` | 43 distritos Lima + usuario admin | 1 |
| `tests/test_auth.py` + `test_datasets.py` | pytest + httpx AsyncClient | 4 |

---

## Variables de entorno (.env)

```env
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/ecolima
SECRET_KEY=cambia-esto-por-32-chars-minimo
ACCESS_TOKEN_EXPIRE_MINUTES=30
AZURE_BLOB_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
AZURE_BLOB_CONTAINER_MODELS=ecolima-models
FRONTEND_URL=http://localhost:5173
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=ecolima@gmail.com
SMTP_PASS=app-password-aqui
ADMIN_EMAIL=admin@ecolima.pe
ADMIN_PASSWORD=AdminSeguro2026!
ML_INFERENCE_THRESHOLD=0.5
ML_PRIORITY_HIGH=0.7
ML_PRIORITY_MEDIUM=0.4
```
