from pydantic import field_validator
from pydantic_settings import BaseSettings

# Orígenes CORS por defecto -- se usan si CORS_ALLOWED_ORIGINS no está seteada
# o queda vacía tras parsearla. La lista de CORS nunca debe quedar vacía por
# una env var faltante (mismo fallo silencioso que tenía SMTP -- ver
# get_cors_origins). Son los orígenes que ya funcionan en producción hoy.
DEFAULT_CORS_ORIGINS: list[str] = [
    "http://localhost:5173",
    "https://mango-cliff-03aa6110f.7.azurestaticapps.net",
]


class Settings(BaseSettings):
    database_url: str
    secret_key: str

    @field_validator("secret_key")
    @classmethod
    def secret_key_min_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY debe tener al menos 32 caracteres")
        return v
    access_token_expire_minutes: int = 30
    azure_blob_connection_string: str
    azure_blob_container_models: str
    # Con default porque el contenedor real ya se llama "datasets" en Azure --
    # no rompe entornos (dev/test/CI) que todavía no la setean explícitamente.
    azure_blob_container_datasets: str = "datasets"
    # Orígenes CORS permitidos, separados por coma
    # (ej. "https://mi-frontend.azurestaticapps.net,http://localhost:5173").
    # Opcional -- si no está seteada o queda vacía, se usa DEFAULT_CORS_ORIGINS
    # (ver get_cors_origins). Así cambiar el dominio del frontend no requiere
    # tocar código ni redesplegar, solo esta env var.
    cors_allowed_origins: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_pass: str | None = None
    admin_email: str
    admin_password: str
    # Solo usada por scripts/create_analista.py (cuenta de prueba local) --
    # opcional porque no todos los entornos crean esa cuenta.
    analista_password: str | None = None
    ml_inference_threshold: float = 0.5
    ml_priority_high: float = 0.7
    ml_priority_medium: float = 0.4
    # Integración con la API de ecolima-ml (servicio local, contrato v0.3)
    ml_api_url: str = "http://localhost:8001"
    ml_api_timeout_seconds: float = 15.0
    # Apagado por defecto -- punto de conmutación preparado, no activado.
    # run_inference sigue usando models/lightgbm_model.pkl mientras esto sea
    # False. Ver TODO en ml_service.run_inference y app/services/ml_zone_mapping.py.
    ml_use_remote_api: bool = False

    def get_cors_origins(self) -> list[str]:
        """Orígenes CORS a permitir: los de CORS_ALLOWED_ORIGINS (coma-separados,
        con espacios y entradas vacías descartados) si hay al menos uno válido,
        o DEFAULT_CORS_ORIGINS en su defecto."""
        if self.cors_allowed_origins:
            origins = [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]
            if origins:
                return origins
        return DEFAULT_CORS_ORIGINS

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
