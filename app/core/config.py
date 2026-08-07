from pydantic import field_validator
from pydantic_settings import BaseSettings

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
    frontend_url: str = "http://localhost:5173"
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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
