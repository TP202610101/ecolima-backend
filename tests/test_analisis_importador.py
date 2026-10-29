"""
tests/test_analisis_importador.py
Validación (0 y 1–15) e importación transaccional del paquete de resultados
al esquema `analisis`: paquete válido y corrupto, idempotencia, conflicto de
contenido, rollback ante un fallo a mitad de carga y versión activa única.

Necesita ECOLIMA_PAQUETE_V1 (ver tests/paquete_utils.py). Las versiones que
crea cada prueba se borran al terminar; las tablas del prototipo no se tocan.
"""
import csv

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.services import analisis_importador as importador
from app.services.analisis_consultas import VersionNoEncontrada, resolver_version
from app.services.analisis_importador import (
    ErrorConflictoVersion,
    ErrorImportacion,
    activar_version,
    importar_paquete,
)
from app.services.analisis_paquete import ErrorPaquete, validar, validar_o_rechazar
from tests.paquete_utils import (
    RUTA_PAQUETE,
    borrar_versiones_desde,
    copiar_paquete,
    editar_csv,
    editar_manifiesto,
    max_version_id,
    requiere_paquete,
    resellar,
)

pytestmark = requiere_paquete

CONTEOS_V1 = {
    "distrito": 43, "sitio": 2242, "sitio_feature": 2242, "tamizaje": 2242, "propension": 2242,
    "criterio_mcda": 2192, "peso_mcda": 3, "mcda": 6576, "mcda_montecarlo": 2192, "concordancia_mcda": 8,
    "fuente": 11, "modelo": 1, "metrica_semilla": 10,
}
TABLAS_DEMO = ("districts", "candidate_zones", "recycling_points", "users", "model_versions", "datasets")


def fallan(paq) -> set[int]:
    return {r.n for r in validar(paq).resultados if not r.ok}


@pytest.fixture
def copia(tmp_path):
    return copiar_paquete(tmp_path / "paquete")


def _version(paq, nueva: str):
    editar_manifiesto(paq, lambda m: m.__setitem__("paquete", nueva))


# ── Validación (sin base de datos) ─────────────────────────────────────────────

def test_paquete_real_pasa_las_validaciones_0_a_15():
    informe = validar(RUTA_PAQUETE)
    assert [r.n for r in informe.resultados] == list(range(0, 16))
    assert informe.ok, [(r.n, r.errores) for r in informe.fallos]


def _fila(df, col, valor, i=0):
    df.loc[i, col] = valor
    return df


def _fila_tope(df):
    i = df.index[(df["esquema"] == "iguales") & (df["rango"] == "1")][0]
    df.loc[i, "en_top_k"] = "False"
    return df


CASOS_CSV = {
    2: ("salidas/mcda_montecarlo.csv", lambda df: _fila(df, "mcda_version", "1.0.1")),
    3: ("datos/fuentes.csv", lambda df: _fila(df, "licencia_estado", "no_verificada")),
    4: ("salidas/tamizaje.csv", lambda df: df[[*df.columns[:5], "T05_sin_punto_existente_30m",
                                               *[c for c in df.columns[5:] if c != "T05_sin_punto_existente_30m"]]]),
    6: ("salidas/features.csv", lambda df: df.drop(index=0)),
    7: ("salidas/propension.csv", lambda df: _fila(df, "rol", "positivo" if df.loc[0, "rol"] == "fondo" else "fondo")),
    8: ("salidas/mcda_montecarlo.csv", lambda df: df.drop(index=0)),
    9: ("salidas/features.csv",
        lambda df: _fila(df, "pob_wp_r500", repr(float(df.loc[0, "pob_wp_r500"]) * (1 + 1e-6)))),
    10: ("salidas/propension_oof_por_semilla.csv", lambda df: _fila(df, "semilla_0", "0.5")),
    11: ("salidas/propension.csv", lambda df: _fila(df, "percentil_oof", "0.123")),
    12: ("salidas/propension.csv", lambda df: _fila(df, "contrib_vias", repr(float(df.loc[0, "contrib_vias"]) + 0.01))),
    13: ("salidas/mcda.csv", _fila_tope),
    14: ("salidas/criterios_mcda.csv", lambda df: _fila(df, "K1_demanda_norm", "1.5")),
    15: ("salidas/sitios.csv", lambda df: _fila(df, "lon", "-80.0")),
}


def test_control_reescribir_sin_cambios_no_falla(copia):
    """Reescribir y resellar sin cambiar valores no provoca fallos: los casos de abajo fallan por su corrupción."""
    for ruta, _ in CASOS_CSV.values():
        editar_csv(copia, ruta, lambda df: df)
    resellar(copia)
    assert fallan(copia) == set()


@pytest.mark.parametrize("n", sorted(CASOS_CSV))
def test_cada_validacion_detecta_su_corrupcion(copia, n):
    ruta, fn = CASOS_CSV[n]
    editar_csv(copia, ruta, fn)
    resellar(copia)
    assert n in fallan(copia)


def test_1_detecta_archivo_alterado_sin_resellar(copia):
    editar_csv(copia, "salidas/sitios.csv", lambda df: _fila(df, "distrito", "OTRO"))
    assert 1 in fallan(copia)


def test_1_detecta_archivo_sin_listar(copia):
    (copia / "salidas" / "extra.csv").write_text("a\n1\n", encoding="utf-8")
    assert 1 in fallan(copia)


def test_5_detecta_estado_viable(copia):
    t = pd.read_csv(copia / "salidas/tamizaje.csv", dtype=str, keep_default_na=False)
    i = t.index[t["estado_tamizaje"] == "apto_tamizaje"][0]
    editar_csv(copia, "salidas/tamizaje.csv", lambda df: _fila(df, "estado_tamizaje", "viable", i))
    resellar(copia)
    r = {x.n: x for x in validar(copia).resultados}
    assert not r[5].ok and any("viable" in e for e in r[5].errores)


@pytest.mark.parametrize(
    "cambio",
    [
        # El modelo oficial es la regresión logística; LightGBM solo es comparación.
        lambda m: m["modelo"].__setitem__("algoritmo", "LGBMClassifier(n_estimators=200)"),
        lambda m: m.__setitem__("paquete", "2.0.0"),
        lambda m: m["modelo"].__setitem__("version", "otro-modelo-1.0.0"),
        lambda m: m["dataset"].__setitem__("sha256", "0" * 64),
        lambda m: m.pop("mcda"),
    ],
    ids=["lightgbm", "version_mayor", "modelo_no_propension", "sha_dataset", "sin_mcda"],
)
def test_0_rechaza_manifiesto_incompatible(copia, cambio):
    editar_manifiesto(copia, cambio)
    assert 0 in fallan(copia)


def test_archivo_ausente_cuenta_como_fallo(copia):
    (copia / "salidas/mcda.csv").unlink()
    assert {1, 2, 4, 8, 13} <= fallan(copia)


def test_validar_o_rechazar_lanza_con_el_detalle(copia):
    editar_csv(copia, *CASOS_CSV[13])
    resellar(copia)
    with pytest.raises(ErrorPaquete) as ex:
        validar_o_rechazar(copia)
    assert [f.n for f in ex.value.fallos] == [13]


def test_directorio_sin_manifiesto(tmp_path):
    with pytest.raises(ErrorPaquete):
        validar(tmp_path)


# ── Importación (base local de pruebas) ────────────────────────────────────────

@pytest_asyncio.fixture
async def engine():
    """Engine de la base de pruebas; al terminar borra las versiones creadas por la prueba."""
    eng = create_async_engine(Settings().database_url, poolclass=NullPool, future=True)
    async with eng.begin() as conn:
        antes = await max_version_id(conn)
        existentes = (await conn.execute(text(
            "SELECT count(*) FROM analisis.version_analisis WHERE paquete_version IN ('1.0.0', '1.0.1')"))).scalar()
    assert existentes == 0, "la base de pruebas ya tiene versiones 1.0.0/1.0.1 importadas; límpiela antes"
    yield eng
    async with eng.begin() as conn:
        await borrar_versiones_desde(conn, antes)
    await eng.dispose()


async def _importar(engine, ruta, **kw):
    async with AsyncSession(engine, expire_on_commit=False) as s:
        return await importar_paquete(s, ruta, **kw)


async def _escalar(engine, sql, **params):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def _conteos_demo(engine) -> dict:
    async with engine.connect() as conn:
        return {t: (await conn.execute(text(f"SELECT count(*) FROM public.{t}"))).scalar() for t in TABLAS_DEMO}


async def test_importa_el_paquete_real_con_los_valores_exactos(engine):
    demo_antes = await _conteos_demo(engine)
    r = await _importar(engine, RUTA_PAQUETE)
    assert (r.estado, r.paquete_version, r.activa) == ("importada", "1.0.0", True)
    assert r.conteos == CONTEOS_V1

    # Precisión completa: el valor guardado es el mismo double que el texto del CSV.
    with open(RUTA_PAQUETE / "salidas/mcda.csv", encoding="utf-8", newline="") as f:
        fila = next(csv.DictReader(f))
    async with engine.connect() as conn:
        puntaje, rango, en_top = (await conn.execute(text(
            "SELECT puntaje, rango, en_top_k FROM analisis.mcda WHERE version_id = :v AND sitio_id = :s "
            "AND esquema = :e"), {"v": r.version_id, "s": fila["sitio_id"], "e": fila["esquema"]})).one()
        assert (puntaje, rango, en_top) == (float(fila["puntaje"]), int(fila["rango"]), fila["en_top_k"] == "True")
        with open(RUTA_PAQUETE / "salidas/sitios.csv", encoding="utf-8", newline="") as f:
            s = next(csv.DictReader(f))
        lon, lat, x, y, ubigeo = (await conn.execute(text(
            "SELECT lon, lat, ST_X(geom), ST_Y(geom), ubigeo FROM analisis.sitio WHERE version_id = :v "
            "AND sitio_id = :s"), {"v": r.version_id, "s": s["sitio_id"]})).one()
        assert (lon, lat, x, y) == (float(s["lon"]), float(s["lat"]), float(s["lon"]), float(s["lat"]))
        assert ubigeo == s["ubigeo"].zfill(6)
        # Sitios dentro de su distrito y trazabilidad del manifiesto.
        fuera = (await conn.execute(text(
            "SELECT count(*) FROM analisis.sitio s JOIN analisis.distrito d USING (version_id, ubigeo) "
            "WHERE s.version_id = :v AND NOT ST_Covers(d.geometry, s.geom)"), {"v": r.version_id})).scalar()
        assert fuera == 0
        sha, ds = (await conn.execute(text(
            "SELECT manifest_sha256, manifest->'dataset'->>'sha256' FROM analisis.version_analisis "
            "WHERE version_id = :v"), {"v": r.version_id})).one()
        assert len(sha) == 64 and ds == "aa6f277925473681eb9d1012ff0eb11065d0f1a7b3d20acfcacfb598e2568154"
    assert await _conteos_demo(engine) == demo_antes


async def test_reimportar_la_misma_version_es_idempotente(engine):
    r1 = await _importar(engine, RUTA_PAQUETE)
    r2 = await _importar(engine, RUTA_PAQUETE)
    assert r2.estado == "ya_importada" and r2.version_id == r1.version_id and r2.activa
    assert r2.conteos == CONTEOS_V1
    assert await _escalar(engine, "SELECT count(*) FROM analisis.version_analisis WHERE paquete_version = '1.0.0'") == 1


async def test_misma_version_con_otro_contenido_es_un_conflicto(engine, copia):
    r1 = await _importar(engine, RUTA_PAQUETE)
    editar_manifiesto(copia, lambda m: m.__setitem__("descripcion", m["descripcion"] + " (modificado)"))
    assert validar(copia).ok
    with pytest.raises(ErrorConflictoVersion):
        await _importar(engine, copia)
    sha = await _escalar(engine, "SELECT manifest_sha256 FROM analisis.version_analisis WHERE version_id = :v",
                         v=r1.version_id)
    assert sha == validar(RUTA_PAQUETE).paquete.manifest_sha256


async def test_paquete_corrupto_no_escribe_nada(engine, copia):
    antes = await _escalar(engine, "SELECT count(*) FROM analisis.version_analisis")
    editar_csv(copia, *CASOS_CSV[13])
    resellar(copia)
    with pytest.raises(ErrorPaquete):
        await _importar(engine, copia)
    assert await _escalar(engine, "SELECT count(*) FROM analisis.version_analisis") == antes


async def test_fallo_a_mitad_de_la_carga_revierte_todo(engine, copia, monkeypatch):
    await _importar(engine, RUTA_PAQUETE)
    sitios_antes = await _escalar(engine, "SELECT count(*) FROM analisis.sitio")
    _version(copia, "1.0.1")

    async def _falla(session, informe, version_id):
        raise RuntimeError("fallo inducido después de cargar distritos y sitios")

    pasos = importador._PASOS_CARGA
    monkeypatch.setattr(importador, "_PASOS_CARGA", [pasos[0], pasos[1], _falla, *pasos[2:]])
    with pytest.raises(RuntimeError):
        await _importar(engine, copia)

    assert await _escalar(engine, "SELECT count(*) FROM analisis.version_analisis "
                                  "WHERE paquete_version = '1.0.1'") == 0
    assert await _escalar(engine, "SELECT count(*) FROM analisis.sitio") == sitios_antes
    assert await _escalar(engine, "SELECT paquete_version FROM analisis.version_analisis WHERE activa") == "1.0.0"


async def test_conteos_distintos_del_paquete_revierten(engine, monkeypatch):
    """Si un paso no carga lo que debía (aquí, el modelo), la importación se rechaza y no queda nada."""
    monkeypatch.setattr(importador, "_PASOS_CARGA", importador._PASOS_CARGA[:-1])
    with pytest.raises(ErrorImportacion):
        await _importar(engine, RUTA_PAQUETE)
    assert await _escalar(engine, "SELECT count(*) FROM analisis.version_analisis "
                                  "WHERE paquete_version = '1.0.0'") == 0


async def test_una_sola_version_activa(engine, copia):
    await _importar(engine, RUTA_PAQUETE)
    _version(copia, "1.0.1")

    r = await _importar(engine, copia, activar=False)
    assert not r.activa
    assert await _escalar(engine, "SELECT paquete_version FROM analisis.version_analisis WHERE activa") == "1.0.0"

    async with AsyncSession(engine) as s:
        await activar_version(s, "1.0.1")
    assert await _escalar(engine, "SELECT count(*) FROM analisis.version_analisis WHERE activa") == 1
    assert await _escalar(engine, "SELECT paquete_version FROM analisis.version_analisis WHERE activa") == "1.0.1"

    async with AsyncSession(engine) as s:
        await activar_version(s, "1.0.0")
        with pytest.raises(ErrorPaquete):
            await activar_version(s, "9.9.9")
    assert await _escalar(engine, "SELECT paquete_version FROM analisis.version_analisis WHERE activa") == "1.0.0"
    assert await _escalar(engine, "SELECT count(*) FROM analisis.version_analisis WHERE activa") == 1


async def test_sin_version_activa_la_consulta_lo_indica(engine):
    await _importar(engine, RUTA_PAQUETE, activar=False)
    async with AsyncSession(engine) as s:
        if (await s.execute(text("SELECT count(*) FROM analisis.version_analisis WHERE activa"))).scalar() == 0:
            with pytest.raises(VersionNoEncontrada):
                await resolver_version(s, None)
        assert (await resolver_version(s, "1.0.0")).paquete_version == "1.0.0"
        with pytest.raises(VersionNoEncontrada):
            await resolver_version(s, "9.9.9")
