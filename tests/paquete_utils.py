"""
tests/paquete_utils.py
Utilidades para las pruebas del paquete de resultados versionado.

Las pruebas que necesitan el paquete real lo buscan en la variable de entorno
ECOLIMA_PAQUETE_V1 (directorio que contiene manifest.json); si no está, se
omiten. El paquete original nunca se modifica: se corrompen copias temporales.
"""
import json
import os
import shutil
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import text

from app.services.analisis_paquete import sha256_archivo

RUTA_PAQUETE = Path(os.environ["ECOLIMA_PAQUETE_V1"]) if os.environ.get("ECOLIMA_PAQUETE_V1") else None

requiere_paquete = pytest.mark.skipif(
    RUTA_PAQUETE is None or not (RUTA_PAQUETE / "manifest.json").is_file(),
    reason="defina ECOLIMA_PAQUETE_V1 con el directorio del paquete v1.0.0 (contiene manifest.json)",
)


def copiar_paquete(destino: Path) -> Path:
    shutil.copytree(RUTA_PAQUETE, destino)
    return destino


def editar_csv(paq: Path, ruta: str, fn) -> None:
    """Modifica un CSV del paquete conservando los valores como texto (fn recibe y devuelve el DataFrame)."""
    df = pd.read_csv(paq / ruta, dtype=str, keep_default_na=False)
    df = fn(df)
    df.to_csv(paq / ruta, index=False, lineterminator="\n")


def editar_manifiesto(paq: Path, fn) -> None:
    man_path = paq / "manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    fn(man)
    man_path.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")


def resellar(paq: Path) -> None:
    """Actualiza sha256 y bytes del manifiesto para que solo falle la validación que se está probando."""
    def _sellar(man):
        for a in man["archivos"]:
            f = paq / a["ruta"]
            a["sha256"], a["bytes"] = sha256_archivo(f), f.stat().st_size
    editar_manifiesto(paq, _sellar)


async def borrar_versiones_desde(conn, version_id_minimo: int) -> None:
    """Borra (en cascada) las versiones creadas por una prueba."""
    await conn.execute(text("DELETE FROM analisis.version_analisis WHERE version_id > :v"), {"v": version_id_minimo})


async def max_version_id(conn) -> int:
    return (await conn.execute(text("SELECT coalesce(max(version_id), 0) FROM analisis.version_analisis"))).scalar_one()
