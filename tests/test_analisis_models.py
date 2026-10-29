"""
tests/test_analisis_models.py
Modelos y restricciones del esquema `analisis` (resultados importados del
paquete versionado). Requiere la base migrada a head.

Cada test trabaja dentro de una transacción externa que se revierte al final:
no deja filas en la base, pase o falle.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from geoalchemy2 import WKTElement
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.db.base import Base
from app.models.analisis import (
    SCHEMA,
    ConcordanciaMcda,
    CriterioMcda,
    DistritoAnalisis,
    Fuente,
    Mcda,
    McdaMontecarlo,
    MetricaSemilla,
    ModeloAnalisis,
    PesoMcda,
    Propension,
    Sitio,
    SitioFeature,
    Tamizaje,
    VersionAnalisis,
)

pytestmark = pytest.mark.asyncio

TABLAS_ANALISIS = {
    "version_analisis", "distrito", "sitio", "sitio_feature", "tamizaje", "propension",
    "criterio_mcda", "peso_mcda", "mcda", "mcda_montecarlo", "concordancia_mcda",
    "fuente", "modelo", "metrica_semilla",
}

# Identificadores con el formato del paquete, marcados como de prueba.
FONDO = "ECO-V0.1-OSM-NODE-PYTEST1"
POSITIVO = "POS-OSM-NPYTEST2"
SHA = "0" * 64


@pytest_asyncio.fixture
async def session():
    """Sesión dentro de una transacción que siempre se revierte."""
    engine = create_async_engine(Settings().database_url, poolclass=NullPool, future=True)
    async with engine.connect() as conn:
        trans = await conn.begin()
        async with AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint") as s:
            yield s
        await trans.rollback()
    await engine.dispose()


def _version(paquete="0.0.0-pytest", activa=False) -> VersionAnalisis:
    return VersionAnalisis(
        paquete_version=paquete,
        dataset_version="1.0.0",
        dataset_sha256=SHA,
        modelo_version="propension-1.0.0",
        mcda_version="1.0.2",
        fecha_paquete=datetime(2026, 10, 3, 15, 34, 38, tzinfo=timezone(timedelta(hours=-5))),
        descripcion="paquete de prueba",
        manifest_sha256=SHA,
        manifest={"paquete": paquete},
        activa=activa,
    )


def _sitio(version_id: int, sitio_id: str, rol: str, lon=-77.03, lat=-12.05) -> Sitio:
    return Sitio(
        version_id=version_id,
        sitio_id=sitio_id,
        lon=lon,
        lat=lat,
        geom=WKTElement(f"POINT({lon} {lat})", srid=4326),
        ubigeo="150101",
        distrito="LIMA",
        clase_oportunidad="paradero_transporte_publico",
        rol=rol,
    )


def _tamizaje_apto(version_id: int, sitio_id: str) -> Tamizaje:
    campo = {c: "pendiente_campo" for c in ("c02", "c03", "c04", "c05", "c06", "c07", "c08", "c09")}
    return Tamizaje(
        version_id=version_id,
        sitio_id=sitio_id,
        t01_territorio="cumple",
        t02_acceso_osm="cumple",
        t03_trazabilidad_documental="cumple_parcial",
        t04_datos_secundarios="cumple",
        dist_punto_existente_m=994.8,
        punto_existente_mas_cercano=POSITIVO,
        t05_sin_punto_existente_30m="cumple",
        estado_tamizaje="apto_tamizaje",
        estado_verificacion_campo="pendiente_campo",
        **campo,
    )


async def _version_con_sitios(session: AsyncSession, **kwargs) -> VersionAnalisis:
    """Versión con un distrito, un sitio de fondo apto y un punto existente."""
    version = _version(**kwargs)
    session.add(version)
    await session.flush()
    vid = version.version_id
    session.add(DistritoAnalisis(
        version_id=vid,
        ubigeo="150101",
        nombre="LIMA",
        geometry=WKTElement(
            "MULTIPOLYGON(((-77.05 -12.07,-77.00 -12.07,-77.00 -12.03,-77.05 -12.03,-77.05 -12.07)))", srid=4326
        ),
    ))
    await session.flush()
    session.add_all([_sitio(vid, POSITIVO, "positivo", -77.031, -12.051), _sitio(vid, FONDO, "fondo")])
    await session.flush()
    return version


async def _completar(session: AsyncSession, vid: int) -> None:
    """Agrega una fila de cada tabla dependiente para el sitio de fondo."""
    session.add_all([
        SitioFeature(
            version_id=vid, sitio_id=FONDO,
            pob_wp_r250=2621.3, pob_wp_r500=10234.9, pob_wp_r1000=43366.6,
            vias_km_km2_r250=18.7, vias_km_km2_r500=20.6, vias_km_km2_r1000=21.5,
            poi_n_km2_r250=356.5, poi_n_km2_r500=297.9, poi_n_km2_r1000=325.0,
            ghsl_frac_construida_r250=0.51, ghsl_frac_construida_r500=0.50, ghsl_frac_construida_r1000=0.49,
            pendiente_grados=4.16, sigersol_gpc_municipal_kg_hab_dia=1.04,
        ),
        _tamizaje_apto(vid, FONDO),
        Tamizaje(version_id=vid, sitio_id=POSITIVO, estado_tamizaje="punto_existente"),
        Propension(
            version_id=vid, sitio_id=FONDO,
            propension_oof=0.4969, propension_oof_de=0.02, percentil_oof=0.5,
            propension_final=0.49, percentil_final=0.5,
            contrib_clase=0.1, contrib_poblacion=0.2, contrib_vias=0.3, contrib_pois=-0.1,
            contrib_construido=0.0, contrib_pendiente=0.05, contrib_sigersol=0.01,
        ),
        CriterioMcda(
            version_id=vid, sitio_id=FONDO,
            k1_demanda_bruto=10234.9, k1_demanda_norm=0.417,
            k2_generacion_bruto=10644.3, k2_generacion_norm=0.392,
            k3_brecha_bruto=994.8, k3_brecha_norm=0.024,
            k4_accesibilidad_bruto=20.6, k4_accesibilidad_norm=0.432,
            contexto_propension_oof=0.4969,
        ),
        PesoMcda(version_id=vid, esquema="iguales", k1_demanda=0.25, k2_generacion=0.25, k3_brecha=0.25,
                 k4_accesibilidad=0.25),
        ConcordanciaMcda(version_id=vid, comparacion="iguales vs entropia", spearman=0.66, kendall_tau_b=0.52,
                         solapamiento_top110=0.08),
        Fuente(
            version_id=vid, codigo="PYTEST-FUENTE", tipo="primaria", nombre="Fuente de prueba",
            institucion="Prueba", licencia="ODbL 1.0", licencia_url="https://opendatacommons.org/licenses/odbl/1-0/",
            licencia_estado="verificada", licencia_verificada_en=date(2026, 10, 3), atribucion="© prueba",
            fecha_corte="2026-09-18", sha256=SHA, bytes=1, uso_en_v1="sitios",
        ),
        ModeloAnalisis(version_id=vid, modelo_version="propension-1.0.0", algoritmo="LogisticRegression",
                       model_card={"tarea": "prueba"}, parametros={"intercepto": 0.0},
                       metricas_resumen={"roc_auc": {"media": 0.749}}),
        MetricaSemilla(version_id=vid, semilla=0, roc_auc=0.75, pr_auc=0.075, boyce=0.758, prevalencia=0.021,
                       recall_top5=0.277, recall_top10=0.426, recall_top20=0.511),
    ])
    await session.flush()
    session.add_all([
        Mcda(version_id=vid, esquema="iguales", sitio_id=FONDO, mcda_version="1.0.2", puntaje=0.316, rango=1,
             en_top_k=True),
        McdaMontecarlo(version_id=vid, sitio_id=FONDO, mcda_version="1.0.2", mc_rango_mediana=1.0,
                       mc_rango_p05=1.0, mc_rango_p95=1.0, mc_frecuencia_top_k=1.0),
    ])
    await session.flush()


async def _rechaza(session: AsyncSession, obj) -> str:
    """Inserta `obj` en un savepoint y devuelve el mensaje de la IntegrityError esperada.

    `SET CONSTRAINTS ALL IMMEDIATE` fuerza también las FK diferidas, que si no
    solo se comprobarían al confirmar (y estos tests nunca confirman).
    """
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            session.add(obj)
            await session.flush()
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return str(exc.value.orig)


# ── Metadata y esquema ────────────────────────────────────────────────────────

async def test_metadata_registra_las_tablas_del_esquema_analisis():
    tablas = {t.name for t in Base.metadata.tables.values() if t.schema == SCHEMA}
    assert tablas == TABLAS_ANALISIS


async def test_la_base_migrada_tiene_las_tablas_del_esquema_analisis(session: AsyncSession):
    res = await session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"), {"s": SCHEMA}
    )
    assert {r[0] for r in res} == TABLAS_ANALISIS


async def test_las_tablas_existentes_no_cambian_de_tipo(session: AsyncSession):
    """La migración es aditiva: districts sigue siendo POLYGON y la V1 usa analisis.distrito."""
    res = await session.execute(text(
        "SELECT f_table_schema, f_table_name, type FROM geometry_columns "
        "WHERE f_table_name IN ('districts', 'distrito', 'sitio') ORDER BY 1, 2"
    ))
    assert [tuple(r) for r in res] == [
        ("analisis", "distrito", "MULTIPOLYGON"),
        ("analisis", "sitio", "POINT"),
        ("public", "districts", "POLYGON"),
    ]


# ── Carga y relaciones ────────────────────────────────────────────────────────

async def test_inserta_una_version_completa_y_navega_relaciones(session: AsyncSession):
    version = await _version_con_sitios(session)
    await _completar(session, version.version_id)
    session.expunge_all()

    v = await session.get(VersionAnalisis, version.version_id)
    sitio = await session.get(Sitio, (v.version_id, FONDO))
    await session.refresh(sitio, ["tamizaje", "propension", "criterio_mcda", "feature", "distrito_ref"])
    assert sitio.tamizaje.estado_tamizaje == "apto_tamizaje"
    assert sitio.tamizaje.punto_existente_mas_cercano == POSITIVO
    assert sitio.propension.percentil_oof == 0.5
    assert sitio.feature.sigersol_gpc_municipal_kg_hab_dia == 1.04
    assert sitio.distrito_ref.nombre == "LIMA"

    criterio = sitio.criterio_mcda
    await session.refresh(criterio, ["mcda", "montecarlo"])
    assert [m.rango for m in criterio.mcda] == [1]
    assert criterio.montecarlo.mc_frecuencia_top_k == 1.0

    await session.refresh(v, ["modelo", "fuentes", "pesos_mcda", "metricas_semilla"])
    assert v.modelo.parametros == {"intercepto": 0.0}
    assert [f.codigo for f in v.fuentes] == ["PYTEST-FUENTE"]
    assert v.licencia_datos == "ODbL-1.0"

    # La geometría del punto se puede consultar espacialmente contra el distrito.
    dentro = await session.scalar(
        select(func.ST_Contains(DistritoAnalisis.geometry, Sitio.geom))
        .join(Sitio, (Sitio.version_id == DistritoAnalisis.version_id) & (Sitio.ubigeo == DistritoAnalisis.ubigeo))
        .where(Sitio.version_id == v.version_id, Sitio.sitio_id == FONDO)
    )
    assert dentro is True


async def test_borrar_la_version_borra_en_cascada(session: AsyncSession):
    version = await _version_con_sitios(session)
    vid = version.version_id
    await _completar(session, vid)

    await session.execute(text("DELETE FROM analisis.version_analisis WHERE version_id = :v"), {"v": vid})
    # Las FK diferidas se comprueban ahora, como al confirmar.
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    for tabla in sorted(TABLAS_ANALISIS):
        n = await session.scalar(text(f"SELECT count(*) FROM analisis.{tabla} WHERE version_id = :v"), {"v": vid})
        assert n == 0, tabla


# ── Restricciones ─────────────────────────────────────────────────────────────

async def test_solo_una_version_activa(session: AsyncSession):
    session.add(_version("0.0.1-pytest", activa=True))
    await session.flush()
    msg = await _rechaza(session, _version("0.0.2-pytest", activa=True))
    assert "uq_version_analisis_activa" in msg


async def test_paquete_version_es_unica(session: AsyncSession):
    session.add(_version("0.0.3-pytest"))
    await session.flush()
    await _rechaza(session, _version("0.0.3-pytest"))


async def test_tamizaje_no_admite_viable(session: AsyncSession):
    version = await _version_con_sitios(session)
    t = _tamizaje_apto(version.version_id, FONDO)
    t.estado_tamizaje = "viable"
    assert "ck_tamizaje_estado" in await _rechaza(session, t)


async def test_tamizaje_excluido_exige_codigo_y_motivo(session: AsyncSession):
    version = await _version_con_sitios(session)
    t = _tamizaje_apto(version.version_id, FONDO)
    t.estado_tamizaje = "excluido_tamizaje"
    t.t05_sin_punto_existente_30m = "no_cumple"
    assert "ck_tamizaje_exclusion" in await _rechaza(session, t)


async def test_tamizaje_punto_existente_debe_ser_un_sitio_de_la_version(session: AsyncSession):
    version = await _version_con_sitios(session)
    t = _tamizaje_apto(version.version_id, FONDO)
    t.punto_existente_mas_cercano = "POS-OSM-NINEXISTENTE"
    assert "fk_tamizaje_punto_existente" in await _rechaza(session, t)


async def test_no_se_puede_borrar_un_punto_existente_referenciado(session: AsyncSession):
    version = await _version_con_sitios(session)
    vid = version.version_id
    await _completar(session, vid)
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            await session.execute(
                text("DELETE FROM analisis.tamizaje WHERE version_id = :v AND sitio_id = :s"), {"v": vid, "s": POSITIVO}
            )
            await session.execute(
                text("DELETE FROM analisis.sitio WHERE version_id = :v AND sitio_id = :s"), {"v": vid, "s": POSITIVO}
            )
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert "fk_tamizaje_punto_existente" in str(exc.value.orig)


async def test_fuente_no_verificada_se_rechaza(session: AsyncSession):
    version = _version("0.0.4-pytest")
    session.add(version)
    await session.flush()
    f = Fuente(
        version_id=version.version_id, codigo="PYTEST-NV", tipo="primaria", nombre="x", institucion="x",
        licencia="x", licencia_estado="no_verificada", licencia_verificada_en=date(2026, 10, 3), atribucion="x",
        fecha_corte="x", sha256=SHA, bytes=0, uso_en_v1="x",
    )
    assert "ck_fuente_licencia_estado" in await _rechaza(session, f)


async def test_pesos_deben_sumar_uno(session: AsyncSession):
    version = _version("0.0.5-pytest")
    session.add(version)
    await session.flush()
    p = PesoMcda(version_id=version.version_id, esquema="iguales", k1_demanda=0.3, k2_generacion=0.25,
                 k3_brecha=0.25, k4_accesibilidad=0.25)
    assert "ck_peso_mcda_suma" in await _rechaza(session, p)


async def test_sitio_fuera_del_recuadro_se_rechaza(session: AsyncSession):
    version = await _version_con_sitios(session)
    assert "ck_sitio_lon" in await _rechaza(
        session, _sitio(version.version_id, "ECO-V0.1-OSM-NODE-PYTEST3", "fondo", lon=-70.0)
    )


async def test_sitio_con_ubigeo_sin_distrito_se_rechaza(session: AsyncSession):
    version = await _version_con_sitios(session)
    s = _sitio(version.version_id, "ECO-V0.1-OSM-NODE-PYTEST4", "fondo")
    s.ubigeo = "150142"
    assert "fk_sitio_distrito" in await _rechaza(session, s)


async def test_percentil_de_propension_cero_se_rechaza(session: AsyncSession):
    version = await _version_con_sitios(session)
    p = Propension(
        version_id=version.version_id, sitio_id=FONDO,
        propension_oof=0.5, propension_oof_de=0.0, percentil_oof=0.0,
        propension_final=0.5, percentil_final=0.5,
        contrib_clase=0, contrib_poblacion=0, contrib_vias=0, contrib_pois=0,
        contrib_construido=0, contrib_pendiente=0, contrib_sigersol=0,
    )
    assert "ck_propension_percentil_oof" in await _rechaza(session, p)


async def test_mcda_exige_criterios_del_sitio(session: AsyncSession):
    """Un sitio sin fila en criterio_mcda (no apto) no puede tener puntaje MCDA."""
    version = await _version_con_sitios(session)
    vid = version.version_id
    session.add(PesoMcda(version_id=vid, esquema="iguales", k1_demanda=0.25, k2_generacion=0.25, k3_brecha=0.25,
                         k4_accesibilidad=0.25))
    await session.flush()
    m = Mcda(version_id=vid, esquema="iguales", sitio_id=POSITIVO, mcda_version="1.0.2", puntaje=0.5, rango=1,
             en_top_k=True)
    assert "fk_mcda_criterio" in await _rechaza(session, m)


async def test_rango_mcda_es_unico_por_esquema(session: AsyncSession):
    version = await _version_con_sitios(session)
    vid = version.version_id
    await _completar(session, vid)
    otro = "ECO-V0.1-OSM-NODE-PYTEST5"
    session.add(_sitio(vid, otro, "fondo"))
    await session.flush()
    session.add(CriterioMcda(
        version_id=vid, sitio_id=otro, k1_demanda_bruto=1, k1_demanda_norm=0, k2_generacion_bruto=1,
        k2_generacion_norm=0, k3_brecha_bruto=1, k3_brecha_norm=0, k4_accesibilidad_bruto=1,
        k4_accesibilidad_norm=0, contexto_propension_oof=0.1,
    ))
    await session.flush()
    m = Mcda(version_id=vid, esquema="iguales", sitio_id=otro, mcda_version="1.0.2", puntaje=0.0, rango=1,
             en_top_k=True)
    assert "uq_mcda_version_esquema_rango" in await _rechaza(session, m)


async def test_montecarlo_exige_p05_mediana_p95_ordenados(session: AsyncSession):
    version = await _version_con_sitios(session)
    vid = version.version_id
    await _completar(session, vid)
    await session.execute(text("DELETE FROM analisis.mcda_montecarlo WHERE version_id = :v"), {"v": vid})
    mc = McdaMontecarlo(version_id=vid, sitio_id=FONDO, mcda_version="1.0.2", mc_rango_mediana=5.0,
                        mc_rango_p05=10.0, mc_rango_p95=20.0, mc_frecuencia_top_k=0.5)
    assert "ck_mcda_montecarlo_rangos" in await _rechaza(session, mc)
