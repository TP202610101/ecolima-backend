"""Protección fail-closed contra bases y servicios remotos en pruebas e importaciones locales.

Solo interpreta URLs: nunca abre una conexión. Se usa en dos lugares:

- `tests/conftest.py`: la suite se aborta antes de importar la app si la base
  no es local y de pruebas, o si Azure Blob / la API ML apuntan fuera de la
  máquina. Así `make test` no puede escribir en Neon ni en Azure por un `.env`
  olvidado.
- `scripts/importar_paquete_analisis.py`: por defecto solo escribe en una base
  local; una base remota exige una opción explícita.

Los mensajes nunca incluyen la URL (puede contener credenciales).
"""

import re
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

HOSTS_LOCALES = frozenset({"localhost", "127.0.0.1", "::1"})

# Marcas de servicios gestionados: se rechazan aunque se hayan escrito en una
# URL con otro nombre de base.
_MARCAS_REMOTAS = ("neon.tech", "neon.build", "azure", "windows.net", "amazonaws.com", "supabase", "render.com")

# El nombre de la base de pruebas debe identificarse como tal.
_PATRON_BD_PRUEBAS = re.compile(r"(prueba|test)", re.IGNORECASE)


def _host(url: str) -> tuple[str | None, str | None]:
    """(host en minúsculas, motivo de rechazo) de una URL SQLAlchemy de PostgreSQL."""
    try:
        u = make_url(url)
    except Exception:
        return None, "la URL de la base no se puede interpretar"
    if not u.drivername.startswith("postgresql"):
        return None, "la base no es PostgreSQL"
    # asyncpg/psycopg admiten el host también en la query (?host=...): no se acepta.
    if any(k.lower() in ("host", "hostaddr") for k in u.query):
        return None, "la URL define el host en los parámetros de conexión"
    host = (u.host or "").lower()
    if any(m in host for m in _MARCAS_REMOTAS):
        return None, "el host es un servicio remoto (Neon, Azure u otra nube)"
    return host, None


def motivo_rechazo_bd_local(url: str | None) -> str | None:
    """None si la URL apunta a un PostgreSQL de esta máquina; si no, el motivo."""
    if not url:
        return "DATABASE_URL no está definida"
    host, motivo = _host(url)
    if motivo:
        return motivo
    if host not in HOSTS_LOCALES:
        return "el host de la base no es local (solo localhost, 127.0.0.1 o ::1)"
    return None


def motivo_rechazo_bd_pruebas(url: str | None) -> str | None:
    """Como `motivo_rechazo_bd_local`, y además el nombre de la base debe ser de pruebas."""
    motivo = motivo_rechazo_bd_local(url)
    if motivo:
        return motivo
    nombre = make_url(url).database or ""
    if not _PATRON_BD_PRUEBAS.search(nombre):
        return "el nombre de la base no la identifica como de pruebas (debe contener «prueba» o «test»)"
    return None


def motivo_rechazo_entorno_pruebas(settings) -> str | None:
    """Comprueba la configuración efectiva de pruebas: base, Azure Blob y API ML."""
    from app.services.blob_storage import is_configured

    motivo = motivo_rechazo_bd_pruebas(settings.database_url)
    if motivo:
        return motivo
    if is_configured(settings.azure_blob_connection_string):
        return "Azure Blob está configurado; las pruebas subirían archivos a Azure"
    host_ml = (urlsplit(settings.ml_api_url).hostname or "").lower()
    if host_ml not in HOSTS_LOCALES:
        return "ML_API_URL no es local; las pruebas llamarían a un servicio remoto"
    return None
