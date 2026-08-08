"""
tests/test_cors_config.py
CORS_ALLOWED_ORIGINS configurable vía env var, en vez de hardcodeada en
main.py -- con fallback seguro a DEFAULT_CORS_ORIGINS si la env var no está
seteada o queda vacía tras parsearla (nunca CORS vacío por una env var
faltante).
"""
from app.core.config import DEFAULT_CORS_ORIGINS, Settings


def test_cors_origins_uses_env_var_when_set(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://foo.example.com,https://bar.example.com")
    settings = Settings()
    assert settings.get_cors_origins() == ["https://foo.example.com", "https://bar.example.com"]


def test_cors_origins_trims_whitespace_and_drops_empty_entries(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", " https://foo.example.com ,, https://bar.example.com ,")
    settings = Settings()
    assert settings.get_cors_origins() == ["https://foo.example.com", "https://bar.example.com"]


def test_cors_origins_falls_back_to_default_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    settings = Settings()
    assert settings.get_cors_origins() == DEFAULT_CORS_ORIGINS


def test_cors_origins_falls_back_to_default_when_env_var_blank(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "   ")
    settings = Settings()
    assert settings.get_cors_origins() == DEFAULT_CORS_ORIGINS


def test_default_cors_origins_includes_current_production_frontend():
    """El fallback debe seguir permitiendo el dominio que hoy funciona en
    producción -- este cambio no puede romper CORS para el frontend actual."""
    assert "https://mango-cliff-03aa6110f.7.azurestaticapps.net" in DEFAULT_CORS_ORIGINS
    assert "http://localhost:5173" in DEFAULT_CORS_ORIGINS
