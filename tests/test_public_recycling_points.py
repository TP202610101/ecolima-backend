"""
tests/test_public_recycling_points.py

Tests de GET /api/v1/public/recycling-points -- la única superficie de la
API sin autenticación junto al modo "cercanos" de /map/points. Trata este
archivo con el mismo cuidado que el endpoint: además de comportamiento,
valida explícitamente que la respuesta NUNCA filtre campos de ml_score /
candidate_zones.

No requiere admin_token -- el endpoint es público. Usa un district_id real
(150101, Miraflores) y un `source` distintivo ("pytest_public_test") para
poder limpiar los puntos que cada test inserta, sin tocar datos reales.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIRAFLORES_DISTRICT_ID = 150101
TEST_SOURCE = "pytest_public_test"

# Punto de referencia (real, cerca de Parque Kennedy, Miraflores)
LAT, LNG = -12.1219, -77.0291


async def _insert_point(
    db_session: AsyncSession,
    lat: float,
    lng: float,
    materials: str,
    address: str = "Punto de prueba",
) -> None:
    await db_session.execute(
        text(
            """
            INSERT INTO recycling_points
                (district_id, latitude, longitude, geometry, address, operator,
                 materials_accepted, verified, source, created_at)
            VALUES
                (:district_id, :lat, :lng,
                 ST_SetSRID(ST_MakePoint(:lng, :lat), 4326),
                 :address, 'Operador de prueba', :materials, true, :source, NOW())
            """
        ),
        {
            "district_id": MIRAFLORES_DISTRICT_ID,
            "lat": lat,
            "lng": lng,
            "address": address,
            "materials": materials,
            "source": TEST_SOURCE,
        },
    )
    await db_session.commit()


async def _cleanup(db_session: AsyncSession) -> None:
    await db_session.execute(text("DELETE FROM recycling_points WHERE source = :s"), {"s": TEST_SOURCE})
    await db_session.commit()


@pytest.mark.asyncio
async def test_returns_nearby_point_with_expected_fields(client: AsyncClient, db_session: AsyncSession):
    await _insert_point(db_session, LAT, LNG, "Papel, Plástico, Vidrio", address="Parque Kennedy")
    try:
        resp = await client.get(
            "/api/v1/public/recycling-points",
            params={"lat": LAT, "lng": LNG, "radius_m": 500},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        point = data["points"][0]
        assert point["nombre"] == "Parque Kennedy"
        assert point["lat"] == LAT
        assert point["lng"] == LNG
        assert set(point["materiales_aceptados"]) == {"Papel", "Plástico", "Vidrio"}
        assert point["distancia_m"] == 0.0
        assert "id" in point
    finally:
        await _cleanup(db_session)


@pytest.mark.asyncio
async def test_area_without_points_returns_200_empty_list_not_404(client: AsyncClient):
    """La mayoría de distritos no tienen puntos cargados -- eso es un 200 con
    lista vacía, nunca un 404."""
    # Punto en medio del océano frente a Lima, lejos de cualquier dato real.
    resp = await client.get(
        "/api/v1/public/recycling-points",
        params={"lat": -12.5, "lng": -77.25, "radius_m": 100},
    )
    assert resp.status_code == 200
    assert resp.json() == {"points": [], "count": 0}


@pytest.mark.asyncio
async def test_lat_out_of_lima_range_returns_422(client: AsyncClient):
    resp = await client.get("/api/v1/public/recycling-points", params={"lat": 5.0, "lng": -77.03})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_lng_out_of_lima_range_returns_422(client: AsyncClient):
    resp = await client.get("/api/v1/public/recycling-points", params={"lat": -12.1, "lng": 10.0})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_radius_over_cap_is_clamped_not_rejected(client: AsyncClient, db_session: AsyncSession):
    """radius_m > 5000 no debe dar 422 -- se clampea a 5000 en silencio."""
    await _insert_point(db_session, LAT + 0.02, LNG, "Metal")  # ~2.2km del punto de referencia
    try:
        resp = await client.get(
            "/api/v1/public/recycling-points",
            params={"lat": LAT, "lng": LNG, "radius_m": 999_999},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
    finally:
        await _cleanup(db_session)


@pytest.mark.asyncio
async def test_response_never_leaks_ml_or_candidate_zone_fields(client: AsyncClient, db_session: AsyncSession):
    """Falla explícitamente si se cuela cualquier campo de ml_score /
    candidate_zones / inferencia en la respuesta pública."""
    await _insert_point(db_session, LAT, LNG, "Papel")
    forbidden_fields = {
        "ml_score",
        "priority_label",
        "is_recommended",
        "recommendation_reason",
        "model_version",
        "is_demo",
        "coverage_gap_m",
        "inference_date",
        "zone_id",
        "income_stratum",
    }
    try:
        resp = await client.get(
            "/api/v1/public/recycling-points",
            params={"lat": LAT, "lng": LNG, "radius_m": 500},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert forbidden_fields.isdisjoint(data.keys())
        for point in data["points"]:
            assert forbidden_fields.isdisjoint(point.keys())
            assert set(point.keys()) == {"id", "nombre", "lat", "lng", "materiales_aceptados", "distancia_m"}
    finally:
        await _cleanup(db_session)


@pytest.mark.asyncio
async def test_material_filter_returns_only_matching_points(client: AsyncClient, db_session: AsyncSession):
    await _insert_point(db_session, LAT, LNG, "Papel, Cartón", address="Solo papel y cartón")
    await _insert_point(db_session, LAT + 0.001, LNG, "Metal", address="Solo metal")
    try:
        resp = await client.get(
            "/api/v1/public/recycling-points",
            params={"lat": LAT, "lng": LNG, "radius_m": 500, "material": "carton"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["points"][0]["nombre"] == "Solo papel y cartón"
    finally:
        await _cleanup(db_session)


@pytest.mark.asyncio
async def test_invalid_material_returns_422_with_allowed_list(client: AsyncClient):
    resp = await client.get(
        "/api/v1/public/recycling-points",
        params={"lat": LAT, "lng": LNG, "material": "unobtainium"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_MATERIAL"


@pytest.mark.asyncio
async def test_rate_limit_30_per_minute(client: AsyncClient):
    responses = []
    for _ in range(31):
        resp = await client.get(
            "/api/v1/public/recycling-points",
            params={"lat": LAT, "lng": LNG, "radius_m": 1},
        )
        responses.append(resp.status_code)

    assert all(code == 200 for code in responses[:30])
    assert responses[30] == 429


@pytest.mark.asyncio
async def test_no_auth_required(client: AsyncClient):
    """Confirma que el endpoint responde sin ningún header de Authorization."""
    resp = await client.get("/api/v1/public/recycling-points", params={"lat": LAT, "lng": LNG})
    assert resp.status_code == 200
