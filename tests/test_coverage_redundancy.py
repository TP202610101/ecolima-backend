"""
tests/test_coverage_redundancy.py
Renombrado de "saturación" -> "redundancia de cobertura" (el cálculo nunca
midió llenado de contenedores -- mide qué % de zonas recomendadas por el
modelo ya tienen un punto real cerca). Cubre: cálculo de redundancy_pct,
flag is_demo, y que las rutas nuevas existan y las viejas ya no.
"""
import re
from pathlib import Path

import pytest
import pytest_asyncio
from geoalchemy2.elements import WKTElement
from httpx import AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.candidate_zone import CandidateZone
from app.models.district import District
from app.services.geo_service import get_coverage_redundancy_data
from tests.conftest import auth_headers

_FAKE_DISTRICT_ID = 999901
_FAKE_POLY = "POLYGON((-77.00 -12.00, -77.00 -12.01, -76.99 -12.01, -76.99 -12.00, -77.00 -12.00))"


@pytest_asyncio.fixture
async def fake_district_with_zones(db_session: AsyncSession):
    """Distrito de prueba aislado (id fuera del rango real de UBIGEO/grid) con
    3 candidate_zones controladas: 2 recomendadas (1 demo+cubierta,
    1 demo+sin cubrir) y 1 no recomendada (no debe contar)."""
    db_session.add(District(
        district_id=_FAKE_DISTRICT_ID,
        district_name="Distrito Prueba Redundancia",
        geometry=WKTElement(_FAKE_POLY, srid=4326),
        area_km2=1.0,
        province="Lima",
        region="Lima Metropolitana",
    ))
    await db_session.flush()

    zones = [
        CandidateZone(
            zone_id=999901, district_id=_FAKE_DISTRICT_ID,
            centroid_lat=-12.005, centroid_lon=-76.995,
            geometry=WKTElement(_FAKE_POLY, srid=4326), cell_area_km2=0.25,
            is_recommended=True, existing_points_500m=1, model_version="v1.0-demo",
        ),
        CandidateZone(
            zone_id=999902, district_id=_FAKE_DISTRICT_ID,
            centroid_lat=-12.006, centroid_lon=-76.996,
            geometry=WKTElement(_FAKE_POLY, srid=4326), cell_area_km2=0.25,
            is_recommended=True, existing_points_500m=0, model_version="v1.0-demo",
        ),
        CandidateZone(
            zone_id=999903, district_id=_FAKE_DISTRICT_ID,
            centroid_lat=-12.007, centroid_lon=-76.997,
            geometry=WKTElement(_FAKE_POLY, srid=4326), cell_area_km2=0.25,
            is_recommended=False, existing_points_500m=0, model_version=None,
        ),
    ]
    for z in zones:
        db_session.add(z)
    await db_session.commit()

    yield _FAKE_DISTRICT_ID

    await db_session.execute(delete(Alert).where(Alert.district_id == _FAKE_DISTRICT_ID))
    await db_session.execute(delete(CandidateZone).where(CandidateZone.district_id == _FAKE_DISTRICT_ID))
    await db_session.execute(delete(District).where(District.district_id == _FAKE_DISTRICT_ID))
    await db_session.commit()


@pytest.mark.asyncio
async def test_redundancy_pct_and_semaphore(db_session: AsyncSession, fake_district_with_zones: int):
    data = await get_coverage_redundancy_data(db_session)
    row = next(d for d in data if d["district_id"] == _FAKE_DISTRICT_ID)

    assert row["total_recommended"] == 2  # la no-recomendada no cuenta
    assert row["already_covered"] == 1
    assert row["redundancy_pct"] == 50.0
    assert row["status"] == "verde"  # 50% <= 50 -> verde


@pytest.mark.asyncio
async def test_is_demo_true_when_model_version_is_demo(
    db_session: AsyncSession, fake_district_with_zones: int
):
    data = await get_coverage_redundancy_data(db_session)
    row = next(d for d in data if d["district_id"] == _FAKE_DISTRICT_ID)
    assert row["is_demo"] is True


@pytest.mark.asyncio
async def test_is_demo_false_when_model_version_is_not_demo(
    db_session: AsyncSession, fake_district_with_zones: int
):
    await db_session.execute(
        text("UPDATE candidate_zones SET model_version = 'v2.1' WHERE district_id = :d"),
        {"d": _FAKE_DISTRICT_ID},
    )
    await db_session.commit()

    data = await get_coverage_redundancy_data(db_session)
    row = next(d for d in data if d["district_id"] == _FAKE_DISTRICT_ID)
    assert row["is_demo"] is False


@pytest.mark.asyncio
async def test_district_without_recommended_zones_is_zero_and_not_demo(db_session: AsyncSession):
    """Un distrito real sin ninguna zona is_recommended=TRUE debe dar 0%,
    verde, is_demo=False -- no un error ni un 'rojo' falso."""
    data = await get_coverage_redundancy_data(db_session)
    zero_rows = [d for d in data if d["total_recommended"] == 0]
    assert zero_rows, "se esperaba al menos un distrito sin zonas recomendadas"
    for row in zero_rows:
        assert row["redundancy_pct"] == 0.0
        assert row["status"] == "verde"
        assert row["is_demo"] is False


@pytest.mark.asyncio
async def test_map_coverage_redundancy_endpoint_exists_and_uses_new_field_names(
    client: AsyncClient, admin_token: str
):
    resp = await client.get(
        "/api/v1/map/coverage-redundancy", headers=auth_headers(admin_token)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    if data:
        assert "redundancy_pct" in data[0]
        assert "is_demo" in data[0]
        assert "saturation_pct" not in data[0]


@pytest.mark.asyncio
async def test_old_map_saturation_route_is_gone(client: AsyncClient, admin_token: str):
    resp = await client.get("/api/v1/map/saturation", headers=auth_headers(admin_token))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_check_coverage_redundancy_endpoint_exists(client: AsyncClient, admin_token: str):
    resp = await client.post(
        "/api/v1/alerts/check-coverage-redundancy", headers=auth_headers(admin_token)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "new_alerts" in data
    assert "districts_flagged" in data


@pytest.mark.asyncio
async def test_old_check_saturation_route_is_gone(client: AsyncClient, admin_token: str):
    resp = await client.post("/api/v1/alerts/check-saturation", headers=auth_headers(admin_token))
    assert resp.status_code == 404


def test_no_saturation_naming_left_in_app_source():
    """Guardrail de regresión: el renombrado debe ser completo en app/ y
    main.py. Los migrations viejos SÍ pueden (deben) seguir mencionando
    saturation_pct -- son historia, no se reescriben."""
    root = Path(__file__).resolve().parents[1]
    pattern = re.compile(r"saturaci|saturation", re.IGNORECASE)
    offenders = []
    for py_file in (root / "app").rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        if pattern.search(py_file.read_text(encoding="utf-8")):
            offenders.append(str(py_file))
    main_py = root / "main.py"
    if pattern.search(main_py.read_text(encoding="utf-8")):
        offenders.append(str(main_py))

    assert not offenders, f"Referencias a 'saturation/saturación' sin renombrar: {offenders}"
