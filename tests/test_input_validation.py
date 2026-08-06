"""
tests/test_input_validation.py

Tests de validación de input agregada en la ronda de hardening de
auditoria-seguridad-backend.md:
  - M5: lat/lon acotados a rangos geográficos válidos en /map/points
  - M4: threshold_m acotado en /geo/recalculate
  - I3: longitud mínima de password al crear un usuario
"""
import pytest
from httpx import AsyncClient

from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


# ── M5: lat/lon fuera de rango -> 422 ───────────────────────────────────────

async def test_map_points_lat_out_of_range_returns_422(client: AsyncClient):
    resp = await client.get("/api/v1/map/points", params={"lat": 999, "lon": 0})
    assert resp.status_code == 422


async def test_map_points_lon_out_of_range_returns_422(client: AsyncClient):
    resp = await client.get("/api/v1/map/points", params={"lat": 0, "lon": -999})
    assert resp.status_code == 422


async def test_map_points_radius_zero_or_negative_returns_422(client: AsyncClient):
    resp = await client.get("/api/v1/map/points", params={"lat": -12.05, "lon": -77.03, "radius_m": 0})
    assert resp.status_code == 422


async def test_map_points_valid_coordinates_still_work(client: AsyncClient):
    """No-breaking: coordenadas válidas de Lima siguen respondiendo 200."""
    resp = await client.get("/api/v1/map/points", params={"lat": -12.0464, "lon": -77.0428})
    assert resp.status_code == 200


# ── M4: threshold_m fuera de rango -> 422 ───────────────────────────────────

async def test_geo_recalculate_threshold_out_of_range_returns_422(client: AsyncClient, admin_token: str):
    resp = await client.post(
        "/api/v1/geo/recalculate",
        params={"threshold_m": 999999},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 422


async def test_geo_recalculate_negative_threshold_returns_422(client: AsyncClient, admin_token: str):
    resp = await client.post(
        "/api/v1/geo/recalculate",
        params={"threshold_m": -5},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 422


# ── I3: password mínima al crear usuario ────────────────────────────────────

async def test_create_user_password_too_short_returns_422(client: AsyncClient, admin_token: str):
    resp = await client.post(
        "/api/v1/admin/users",
        json={"email": "pytest_short_pw@example.com", "password": "abc123", "role": "analista"},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 422
