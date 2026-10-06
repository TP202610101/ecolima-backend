"""
tests/test_geo_coverage_gap.py
calculate_coverage_gaps mide coverage_gap_m/dist_to_nearest_point_m desde el
CENTROIDE de la zona (ST_Centroid), no desde el borde del polígono. Antes
usaba ST_Distance directo contra el polígono, que da 0 apenas el punto de
reciclaje cae DENTRO de la celda -- en la práctica, la mayoría de zonas
recomendadas quedaban "Dentro del área · 0 m" en el frontend.
"""
import pytest
import pytest_asyncio
from geoalchemy2.elements import WKTElement
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_zone import CandidateZone
from app.models.district import District
from app.models.recycling_point import RecyclingPoint
from app.services.geo_service import calculate_coverage_gaps

pytestmark = pytest.mark.asyncio

# Coordenadas deliberadamente lejos del bounding box real de Lima
# (LAT_RANGE/LON_RANGE de dataset_service.py) para que ningún recycling_point
# real de la BD interfiera con el MIN() de la query -- calculate_coverage_gaps
# no filtra por distrito, corre sobre TODA la tabla.
_FAKE_DISTRICT_ID = 999905
_FAKE_ZONE_ID = 999905
_FAKE_POLY = "POLYGON((-70.000 -1.000, -70.000 -1.010, -69.990 -1.010, -69.990 -1.000, -70.000 -1.000))"
# Punto estrictamente DENTRO del polígono, cerca de una esquina -- ni en el
# centroide ni en el borde. Es el caso exacto que antes daba 0.
_POINT_INSIDE_LON = -69.999
_POINT_INSIDE_LAT = -1.001


@pytest_asyncio.fixture
async def fake_zone_with_interior_point(db_session: AsyncSession):
    db_session.add(District(
        district_id=_FAKE_DISTRICT_ID,
        district_name="Distrito Prueba Coverage Gap",
        geometry=WKTElement(_FAKE_POLY, srid=4326),
        area_km2=1.0,
        province="Lima",
        region="Lima Metropolitana",
    ))
    await db_session.flush()

    db_session.add(CandidateZone(
        zone_id=_FAKE_ZONE_ID,
        district_id=_FAKE_DISTRICT_ID,
        centroid_lat=-1.005,
        centroid_lon=-69.995,
        geometry=WKTElement(_FAKE_POLY, srid=4326),
        cell_area_km2=1.0,
    ))
    db_session.add(RecyclingPoint(
        district_id=_FAKE_DISTRICT_ID,
        latitude=_POINT_INSIDE_LAT,
        longitude=_POINT_INSIDE_LON,
        geometry=WKTElement(f"POINT({_POINT_INSIDE_LON} {_POINT_INSIDE_LAT})", srid=4326),
        source="pytest_coverage_gap_fix",
    ))
    await db_session.commit()

    yield _FAKE_ZONE_ID

    await db_session.execute(delete(RecyclingPoint).where(RecyclingPoint.district_id == _FAKE_DISTRICT_ID))
    await db_session.execute(delete(CandidateZone).where(CandidateZone.zone_id == _FAKE_ZONE_ID))
    await db_session.execute(delete(District).where(District.district_id == _FAKE_DISTRICT_ID))
    await db_session.commit()


async def test_coverage_gap_is_positive_when_point_is_inside_the_polygon(
    db_session: AsyncSession, fake_zone_with_interior_point: int
):
    """Antes del fix, un punto DENTRO del polígono daba coverage_gap_m = 0
    (ST_Distance contra el polígono). Con ST_Centroid, debe dar > 0 siempre
    que el punto no esté exactamente en el centroide -- que es el caso acá."""
    await calculate_coverage_gaps(db_session)

    # Raw SQL, no el ORM: calculate_coverage_gaps hace un UPDATE por text(),
    # que no actualiza el identity map de la sesión -- .get() devolvería el
    # objeto cacheado (coverage_gap_m=None de antes del commit).
    row = (
        await db_session.execute(
            text("SELECT coverage_gap_m, dist_to_nearest_point_m FROM candidate_zones WHERE zone_id = :id"),
            {"id": _FAKE_ZONE_ID},
        )
    ).first()

    assert row is not None
    assert row.coverage_gap_m is not None
    assert row.coverage_gap_m > 0, (
        f"coverage_gap_m debería ser > 0 (punto dentro del polígono, lejos del "
        f"centroide), dio {row.coverage_gap_m}"
    )
    assert row.dist_to_nearest_point_m == row.coverage_gap_m
