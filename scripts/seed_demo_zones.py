"""Seed de DEMO para probar el flujo ML completo en local.

Puebla lo que las migraciones/seeds no cubren:
  1. candidate_zones: 250 zonas sintéticas con las 20 features de Sección B
     (mismas distribuciones que generate_synthetic_training_data, seed 42).
  2. model_versions: registra y activa "v1-local-demo" para que el Panel ML
     muestre una versión activa y run-inference pueda correr con
     models/lightgbm_model.pkl.

Idempotente: si ya hay zonas o la versión existe, no duplica.

ADVERTENCIA: datos sintéticos de desarrollo técnico. No son evidencia
empírica de la tesis.

Uso (desde la raíz de ecolima-backend, con la DB levantada):
    python scripts/seed_demo_zones.py
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from geoalchemy2 import WKTElement
from sqlalchemy import func, select, text, update

from app.db.session import AsyncSessionLocal
from app.models.candidate_zone import CandidateZone
from app.models.model_version import ModelVersion
from app.services.ml_service import FEATURE_COLUMNS, generate_synthetic_training_data

N_ZONES = 250
VERSION_NAME = "v1-local-demo"
HALF_CELL_DEG = 0.001125  # ~125 m => celda ~250 m x 250 m
CELL_AREA_KM2 = 0.0625


async def seed_zones(db) -> int:
    count = (await db.execute(select(func.count()).select_from(CandidateZone))).scalar()
    if count and count > 0:
        print(f"candidate_zones ya tiene {count} filas — no se insertan zonas.")
        return 0

    district_ids = [r[0] for r in (await db.execute(text("SELECT district_id FROM districts ORDER BY district_id"))).fetchall()]
    if not district_ids:
        print("ERROR: no hay distritos. Corre primero los seeds (instalar_dependencias.bat).")
        return -1

    df = generate_synthetic_training_data(n=N_ZONES)
    rng = np.random.default_rng(seed=7)

    inserted = 0
    for _, row in df.iterrows():
        lat, lon = float(row["centroid_lat"]), float(row["centroid_lon"])
        h = HALF_CELL_DEG
        wkt = (
            f"POLYGON(({lon - h} {lat - h}, {lon + h} {lat - h}, "
            f"{lon + h} {lat + h}, {lon - h} {lat + h}, {lon - h} {lat - h}))"
        )
        zone = CandidateZone(
            centroid_lat=lat,
            centroid_lon=lon,
            geometry=WKTElement(wkt, srid=4326),
            district_id=int(rng.choice(district_ids)),
            cell_area_km2=CELL_AREA_KM2,
            existing_points_500m=int(row["existing_points_500m"]),
            has_park_300m=bool(row["has_park_300m"]),
            is_suitable=None,  # sin etiqueta: son zonas a inferir
        )
        for col in FEATURE_COLUMNS:
            if col in ("existing_points_500m", "has_park_300m"):
                continue
            value = row[col]
            if col in ("income_stratum", "informal_recyclers_count"):
                value = int(value)
            else:
                value = float(value)
            setattr(zone, col, value)
        db.add(zone)
        inserted += 1

    await db.commit()
    print(f"Insertadas {inserted} zonas candidatas sintéticas.")
    return inserted


async def seed_model_version(db) -> None:
    existing = (
        await db.execute(select(ModelVersion).where(ModelVersion.version_name == VERSION_NAME))
    ).scalar_one_or_none()
    if existing is not None:
        if not existing.is_active:
            await db.execute(update(ModelVersion).values(is_active=False))
            existing.is_active = True
            await db.commit()
            print(f"Versión '{VERSION_NAME}' ya existía — reactivada.")
        else:
            print(f"Versión '{VERSION_NAME}' ya existe y está activa.")
        return

    await db.execute(update(ModelVersion).values(is_active=False))
    db.add(
        ModelVersion(
            version_name=VERSION_NAME,
            training_date=datetime.utcnow(),
            artifact_url="local://models/lightgbm_model.pkl",
            metrics=json.dumps(
                {
                    "accuracy": None,
                    "f1": None,
                    "note": "DEMO local con datos sinteticos — sin metricas reales",
                }
            ),
            features_used=json.dumps(FEATURE_COLUMNS),
            is_active=True,
        )
    )
    await db.commit()
    print(f"Versión '{VERSION_NAME}' registrada y activada (modelo: models/lightgbm_model.pkl).")


async def main() -> None:
    model_path = Path(__file__).resolve().parents[1] / "models" / "lightgbm_model.pkl"
    if not model_path.exists():
        print(f"AVISO: no se encontró {model_path} — la inferencia fallará hasta colocarlo.")

    async with AsyncSessionLocal() as db:
        result = await seed_zones(db)
        if result == -1:
            sys.exit(1)
        await seed_model_version(db)

    print()
    print("Listo. Ahora en el Panel ML: 'Actualizar modelo desde storage' /")
    print("ejecutar inferencia, y las recomendaciones apareceran en Reportes.")


if __name__ == "__main__":
    asyncio.run(main())
