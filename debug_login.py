import traceback

from fastapi.testclient import TestClient

from app.core.config import Settings
from main import app

client = TestClient(app, raise_server_exceptions=True)
settings = Settings()

try:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    print(response.status_code)
    print(response.text)
except Exception:
    traceback.print_exc()
