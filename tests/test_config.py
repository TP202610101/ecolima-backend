"""
tests/test_config.py

SMTP_PASS llega del .env o de Azure App Settings tal como la persona la haya
pegado -- Google muestra la contraseña de aplicación en 4 grupos de 4
caracteres separados por espacio para que se lea, pero smtplib necesita los
16 caracteres juntos. Settings debe limpiarla sola (ver smtp_pass_sin_espacios
en app/core/config.py), para que un pegado tal cual no rompa el envío.
"""
from app.core.config import Settings

_CAMPOS_OBLIGATORIOS = dict(
    database_url="postgresql+asyncpg://postgres:password@localhost:5433/ecolima_test",
    secret_key="x" * 32,
    azure_blob_connection_string="",
    azure_blob_container_models="models",
    admin_email="admin@example.com",
    admin_password="x",
    frontend_base_url="http://localhost:5173",
)


def test_smtp_pass_con_espacios_en_grupos_de_cuatro_queda_unida():
    settings = Settings(**_CAMPOS_OBLIGATORIOS, smtp_pass="abcd efgh ijkl mnop")
    assert settings.smtp_pass == "abcdefghijklmnop"


def test_smtp_pass_con_espacios_al_inicio_y_final_tambien_se_limpia():
    settings = Settings(**_CAMPOS_OBLIGATORIOS, smtp_pass="  abcd efgh ijkl mnop  ")
    assert settings.smtp_pass == "abcdefghijklmnop"


def test_smtp_pass_sin_espacios_no_cambia():
    settings = Settings(**_CAMPOS_OBLIGATORIOS, smtp_pass="abcdefghijklmnop")
    assert settings.smtp_pass == "abcdefghijklmnop"


def test_smtp_pass_none_se_mantiene_none():
    settings = Settings(**_CAMPOS_OBLIGATORIOS, smtp_pass=None)
    assert settings.smtp_pass is None
