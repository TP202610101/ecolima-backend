from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    secret_key: str
    access_token_expire_minutes: int = 30
    azure_blob_connection_string: str
    azure_blob_container_models: str
    frontend_url: str = "http://localhost:5173"
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_pass: str | None = None
    admin_email: str
    admin_password: str
    ml_inference_threshold: float = 0.5
    ml_priority_high: float = 0.7
    ml_priority_medium: float = 0.4

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
