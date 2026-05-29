import json
import traceback
from fastapi.testclient import TestClient

from main import app

client = TestClient(app, raise_server_exceptions=True)

try:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@ecolima.pe", "password": "AdminSeguro2026!"},
    )
    print(response.status_code)
    print(response.text)
except Exception:
    traceback.print_exc()
