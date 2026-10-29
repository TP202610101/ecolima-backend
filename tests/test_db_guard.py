"""
tests/test_db_guard.py
Protección fail-closed de app/core/db_guard.py: las pruebas y el importador
local rechazan bases remotas o compartidas sin conectarse a ellas.

Las URLs remotas de abajo son ficticias; la protección solo las interpreta.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.db_guard import (
    motivo_rechazo_bd_local,
    motivo_rechazo_bd_pruebas,
    motivo_rechazo_entorno_pruebas,
)

NEON_FICTICIA = "postgresql+asyncpg://usuario:clave-ficticia@ep-ficticio-000000.us-east-2.aws.neon.tech/neondb"
AZURE_FICTICIA = "postgresql+asyncpg://usuario:clave-ficticia@ecolima-ficticia.postgres.database.azure.com/ecolima"
LOCAL_PRUEBAS = "postgresql+asyncpg://postgres:clave@127.0.0.1:55432/ecolima_pruebas"

BLOB_PLACEHOLDER = "DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net"


@pytest.mark.parametrize(
    "url",
    [
        NEON_FICTICIA,
        AZURE_FICTICIA,
        # Nombre «de pruebas» no basta si el host es remoto.
        "postgresql+asyncpg://u:p@ep-x.neon.tech/ecolima_pruebas",
        "postgresql+asyncpg://u:p@db.ejemplo.org:5432/ecolima_pruebas",
        "postgresql+asyncpg://u:p@10.0.0.5/ecolima_test",
        # Host local escondido en los parámetros de conexión.
        "postgresql+asyncpg://u:p@127.0.0.1/ecolima_pruebas?host=ep-x.neon.tech",
        # Base local que no se identifica como de pruebas (p. ej. la del docker-compose).
        "postgresql+asyncpg://postgres:password@localhost:5433/ecolima",
        "sqlite:///pruebas.db",
        "no es una url",
        "",
        None,
    ],
)
def test_rechaza_bases_no_locales_o_no_de_pruebas(url):
    assert motivo_rechazo_bd_pruebas(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        LOCAL_PRUEBAS,
        "postgresql+asyncpg://u:p@localhost/ecolima_test",
        "postgresql://u:p@[::1]:5432/pruebas",
    ],
)
def test_acepta_base_local_de_pruebas(url):
    assert motivo_rechazo_bd_pruebas(url) is None


def test_el_motivo_no_revela_la_url():
    motivo = motivo_rechazo_bd_pruebas(NEON_FICTICIA)
    assert "clave-ficticia" not in motivo and "neon.tech" not in motivo


def test_importador_local_no_exige_nombre_de_pruebas_pero_si_host_local():
    assert motivo_rechazo_bd_local("postgresql+asyncpg://postgres:p@127.0.0.1:5433/ecolima") is None
    assert motivo_rechazo_bd_local(NEON_FICTICIA) is not None
    assert motivo_rechazo_bd_local(AZURE_FICTICIA) is not None


def _settings(**cambios):
    base = {
        "database_url": LOCAL_PRUEBAS,
        "azure_blob_connection_string": BLOB_PLACEHOLDER,
        "ml_api_url": "http://127.0.0.1:9",
    }
    return SimpleNamespace(**(base | cambios))


def test_entorno_rechaza_azure_blob_y_api_ml_remotos():
    assert motivo_rechazo_entorno_pruebas(_settings()) is None
    real = "DefaultEndpointsProtocol=https;AccountName=ficticia;AccountKey=Zm9v;EndpointSuffix=core.windows.net"
    assert "Azure Blob" in motivo_rechazo_entorno_pruebas(_settings(azure_blob_connection_string=real))
    assert "ML_API_URL" in motivo_rechazo_entorno_pruebas(_settings(ml_api_url="https://ecolima-ml.azurewebsites.net"))


def test_pytest_se_aborta_sin_conectar_con_una_url_remota_ficticia(tmp_path):
    """La suite completa, lanzada con una URL de Neon ficticia, termina antes de importar la app."""
    raiz = Path(__file__).resolve().parents[1]
    env = os.environ | {"DATABASE_URL": NEON_FICTICIA}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_db_guard.py", "-q", "-p", "no:cacheprovider",
         f"--basetemp={tmp_path}"],
        cwd=raiz, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    salida = r.stdout + r.stderr
    # pytest informa la salida durante la carga del conftest como error de uso (4).
    assert r.returncode != 0, salida
    assert "Pruebas abortadas antes de conectar" in salida
    assert "clave-ficticia" not in salida
    assert " passed" not in salida
