"""
tests/test_analisis_endpoints.py
GET /api/v1/analisis/*: respuestas con los datos del paquete importado,
filtros, paginación, GeoJSON, atribución y reglas de lectura (nada «viable»,
sin categorías Alta/Media/Baja, propensión como contexto).

Necesita ECOLIMA_PAQUETE_V1 (ver tests/paquete_utils.py). El paquete se
importa una vez para el módulo y se borra al terminar.
"""
import asyncio
import re

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.services.analisis_importador import importar_paquete
from tests.conftest import auth_headers
from tests.paquete_utils import RUTA_PAQUETE, borrar_versiones_desde, max_version_id, requiere_paquete

pytestmark = requiere_paquete

API = "/api/v1/analisis"
VIABLE = re.compile(r"\bviable\b", re.IGNORECASE)


@pytest.fixture(scope="module", autouse=True)
def paquete_importado():
    """Importa el paquete v1.0.0 (activo) en la base de pruebas; lo borra al terminar el módulo."""
    loop = asyncio.new_event_loop()
    engine = create_async_engine(Settings().database_url, poolclass=NullPool, future=True)

    async def _importar():
        async with engine.begin() as conn:
            antes = await max_version_id(conn)
        async with AsyncSession(engine, expire_on_commit=False) as s:
            r = await importar_paquete(s, RUTA_PAQUETE, activar=True)
        assert r.estado == "importada", "la base de pruebas ya tenía la versión 1.0.0; límpiela antes"
        return antes

    async def _borrar(antes):
        async with engine.begin() as conn:
            await borrar_versiones_desde(conn, antes)
        await engine.dispose()

    antes = loop.run_until_complete(_importar())
    try:
        yield
    finally:
        loop.run_until_complete(_borrar(antes))
        loop.close()


@pytest.fixture(scope="module")
def csv_paquete():
    leer = lambda r: pd.read_csv(RUTA_PAQUETE / r, dtype=str, keep_default_na=False)  # noqa: E731
    return {r: leer(f"salidas/{r}.csv")
            for r in ("sitios", "tamizaje", "propension", "mcda", "mcda_montecarlo", "criterios_mcda")}


async def _get(client, token, ruta, **params):
    r = await client.get(f"{API}{ruta}", params=params, headers=auth_headers(token))
    assert not VIABLE.search(r.text), f"«viable» en {ruta}"
    return r


def _atribucion_ok(body):
    a = body["atribucion"]
    assert a["licencia"] == "ODbL-1.0" and a["licencia_url"].startswith("https://opendatacommons.org/")
    assert "OpenStreetMap" in a["aviso"] and a["fuentes"] == f"{API}/fuentes" and a["paquete_version"] == "1.0.0"


async def test_requiere_autenticacion(client):
    for ruta in ("/versiones", "/sitios", "/sitios/geojson", "/distritos", "/fuentes", "/modelo"):
        assert (await client.get(f"{API}{ruta}")).status_code in (401, 403)


async def test_versiones_y_version_activa(client, admin_token):
    r = await _get(client, admin_token, "/versiones")
    assert r.status_code == 200
    body = r.json()
    assert body["activa"] == "1.0.0"
    v = next(x for x in body["versiones"] if x["paquete_version"] == "1.0.0")
    assert (v["modelo_version"], v["mcda_version"], v["licencia_datos"]) == ("propension-1.0.0", "1.0.2", "ODbL-1.0")
    assert v["fecha_paquete"].endswith("-05:00")

    r = await _get(client, admin_token, "/versiones/activa")
    assert r.status_code == 200 and r.headers["x-licencia-datos"] == "ODbL-1.0"
    d = r.json()
    assert d["conteos"]["sitios"] == 2242 and d["conteos"]["positivos"] == 47 and d["conteos"]["distritos"] == 43
    assert d["conteos"]["estado_tamizaje"] == {"apto_tamizaje": 2192, "excluido_tamizaje": 3, "punto_existente": 47}
    assert d["modelo"]["algoritmo"].startswith("LogisticRegression")
    assert d["mcda"]["top_k"] == 110 and d["mcda"]["esquema_principal"] == "iguales"
    assert len(d["archivos"]) == 20 and all(len(a["sha256"]) == 64 for a in d["archivos"])
    assert "responsable" not in r.text
    _atribucion_ok(d)


async def test_version_inexistente_404(client, admin_token):
    assert (await _get(client, admin_token, "/sitios", version="9.9.9")).status_code == 404
    assert (await _get(client, admin_token, "/versiones/activa", version="9.9.9")).status_code == 404


async def test_sitios_paginacion_por_rango(client, admin_token):
    p1 = (await _get(client, admin_token, "/sitios", limit=50, offset=0)).json()
    p2 = (await _get(client, admin_token, "/sitios", limit=50, offset=50)).json()
    assert p1["total"] == p2["total"] == 2242 and len(p1["items"]) == len(p2["items"]) == 50
    assert [i["mcda"]["rango"] for i in p1["items"] + p2["items"]] == list(range(1, 101))
    assert not {i["sitio_id"] for i in p1["items"]} & {i["sitio_id"] for i in p2["items"]}
    _atribucion_ok(p1)
    ultima = (await _get(client, admin_token, "/sitios", limit=10, offset=2240)).json()
    assert len(ultima["items"]) == 2
    assert (await _get(client, admin_token, "/sitios", limit=501)).status_code == 422
    assert (await _get(client, admin_token, "/sitios", offset=-1)).status_code == 422
    por_id = (await _get(client, admin_token, "/sitios", orden="sitio_id", limit=5)).json()["items"]
    assert [i["sitio_id"] for i in por_id] == sorted(i["sitio_id"] for i in por_id)


async def test_sitios_filtros(client, admin_token, csv_paquete):
    async def total(**p):
        r = await _get(client, admin_token, "/sitios", limit=500, **p)
        assert r.status_code == 200, r.text
        return r.json()

    assert (await total(estado_tamizaje="apto_tamizaje"))["total"] == 2192
    excl = await total(estado_tamizaje="excluido_tamizaje")
    assert excl["total"] == 3 and all(i["codigo_exclusion"] == "T05" and i["mcda"] is None for i in excl["items"])
    pos = await total(rol="positivo")
    assert pos["total"] == 47 and all(i["estado_tamizaje"] == "punto_existente" and i["mcda"] is None
                                      for i in pos["items"])
    for esquema in ("iguales", "critic", "entropia"):
        top = await total(en_top_k="true", esquema=esquema)
        assert top["total"] == 110 and top["esquema"] == esquema
        assert sorted(i["mcda"]["rango"] for i in top["items"]) == list(range(1, 111))
    s = csv_paquete["sitios"]
    ubigeo = s["ubigeo"].value_counts().index[0]
    por_ubigeo = await total(ubigeo=ubigeo)
    assert por_ubigeo["total"] == int((s["ubigeo"] == ubigeo).sum())
    assert {i["ubigeo"] for i in por_ubigeo["items"]} == {ubigeo}
    clase = await total(clase_oportunidad="parque", rol="fondo")
    assert clase["total"] == int(((s["clase_oportunidad"] == "parque") & (s["rol"] == "fondo")).sum())


async def test_sitios_bbox(client, admin_token, csv_paquete):
    caja = (-77.06, -12.11, -77.02, -12.08)
    r = (await _get(client, admin_token, "/sitios", bbox=",".join(map(str, caja)), limit=500)).json()
    s = csv_paquete["sitios"]
    lon, lat = s["lon"].astype(float), s["lat"].astype(float)
    assert r["total"] == int((lon.between(caja[0], caja[2]) & lat.between(caja[1], caja[3])).sum()) > 0
    assert all(caja[0] <= i["lon"] <= caja[2] and caja[1] <= i["lat"] <= caja[3] for i in r["items"])
    for malo in ("1,2,3", "a,b,c,d", "-77,-12,-78,-11"):
        assert (await _get(client, admin_token, "/sitios", bbox=malo)).status_code == 422


async def test_filtros_invalidos_422(client, admin_token):
    for params in ({"estado_tamizaje": "viable"}, {"rol": "negativo"}, {"esquema": "pesos_propios"},
                   {"ubigeo": "999999"}, {"orden": "propension"}):
        assert (await client.get(f"{API}/sitios", params=params, headers=auth_headers(admin_token))).status_code == 422


async def test_sitios_coinciden_con_el_paquete(client, admin_token, csv_paquete):
    """El backend no recalcula: puntaje, rango, percentil y Monte Carlo son los del paquete."""
    items = (await _get(client, admin_token, "/sitios", limit=500, estado_tamizaje="apto_tamizaje")).json()["items"]
    m = csv_paquete["mcda"]
    m = m[m["esquema"] == "iguales"].set_index("sitio_id")
    p = csv_paquete["propension"].set_index("sitio_id")
    mc = csv_paquete["mcda_montecarlo"].set_index("sitio_id")
    for i in items:
        sid = i["sitio_id"]
        assert i["mcda"] == {"puntaje": float(m.loc[sid, "puntaje"]), "rango": int(m.loc[sid, "rango"]),
                             "en_top_k": m.loc[sid, "en_top_k"] == "True"}
        assert i["propension_percentil"] == float(p.loc[sid, "percentil_oof"])
        assert i["mc_frecuencia_top_k"] == float(mc.loc[sid, "mc_frecuencia_top_k"])


async def _todos_los_sitios(client, token, ruta="/sitios", **params):
    items, offset = [], 0
    while True:
        p = (await _get(client, token, ruta, limit=500, offset=offset, **params)).json()
        items += p["items"]
        offset += 500
        if offset >= p["total"]:
            return items


async def test_sitios_traen_distancia_y_criterios_del_paquete(client, admin_token, csv_paquete):
    """Ampliación 2026-10-10: la lista trae la distancia al punto existente y K1–K4 tal como están en el paquete."""
    items = await _todos_los_sitios(client, admin_token)
    assert len(items) == 2242
    t = csv_paquete["tamizaje"].set_index("sitio_id")
    c = csv_paquete["criterios_mcda"].set_index("sitio_id")
    for i in items:
        sid, fila = i["sitio_id"], t.loc[i["sitio_id"]]
        esperado_d = None if fila["dist_punto_existente_m"] == "" else float(fila["dist_punto_existente_m"])
        assert i["dist_punto_existente_m"] == esperado_d
        assert i["punto_existente_mas_cercano"] == (fila["punto_existente_mas_cercano"] or None)
        if i["mcda"] is None:
            assert i["criterios_mcda"] is None
        else:
            assert set(i["criterios_mcda"]) == {"K1_demanda", "K2_generacion", "K3_brecha", "K4_accesibilidad"}
            for k, v in i["criterios_mcda"].items():
                assert v == {"bruto": float(c.loc[sid, f"{k}_bruto"]), "norm": float(c.loc[sid, f"{k}_norm"])}
    aptos = [i for i in items if i["estado_tamizaje"] == "apto_tamizaje"]
    assert len(aptos) == 2192 and all(i["criterios_mcda"] is not None for i in aptos)


async def test_lista_y_detalle_coinciden_en_los_campos_ampliados(client, admin_token):
    """Mismos valores en /sitios, /sitios/geojson y /sitios/{id}; los campos previos no cambian de forma."""
    p = (await _get(client, admin_token, "/sitios", limit=3, ubigeo="150131")).json()
    g = (await _get(client, admin_token, "/sitios/geojson", ubigeo="150131")).json()
    por_id = {f["id"]: f["properties"] for f in g["features"]}
    for i in p["items"]:
        d = (await _get(client, admin_token, f"/sitios/{i['sitio_id']}")).json()
        assert i["dist_punto_existente_m"] == d["tamizaje"]["dist_punto_existente_m"]
        assert i["punto_existente_mas_cercano"] == d["tamizaje"]["punto_existente_mas_cercano"]
        assert i["criterios_mcda"] == d["criterios_mcda"]
        assert por_id[i["sitio_id"]]["criterios_mcda"] == i["criterios_mcda"]
        assert {"sitio_id", "lon", "lat", "ubigeo", "distrito", "clase_oportunidad", "rol", "estado_tamizaje",
                "codigo_exclusion", "propension_percentil", "mcda", "mc_frecuencia_top_k"} <= set(i)


async def test_sitios_geojson(client, admin_token, csv_paquete):
    r = await _get(client, admin_token, "/sitios/geojson")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/geo+json")
    assert r.headers["x-licencia-datos"] == "ODbL-1.0"
    g = r.json()
    assert g["type"] == "FeatureCollection" and len(g["features"]) == 2242
    _atribucion_ok(g)
    s = csv_paquete["sitios"].set_index("sitio_id")
    for f in g["features"][:200]:
        assert f["geometry"]["type"] == "Point"
        assert f["geometry"]["coordinates"] == [float(s.loc[f["id"], "lon"]), float(s.loc[f["id"], "lat"])]
    filtrado = (await _get(client, admin_token, "/sitios/geojson", en_top_k="true")).json()
    assert len(filtrado["features"]) == 110


async def test_detalle_de_sitio(client, admin_token, csv_paquete):
    t = csv_paquete["tamizaje"]
    apto = t.loc[t["estado_tamizaje"] == "apto_tamizaje", "sitio_id"].iloc[0]
    d = (await _get(client, admin_token, f"/sitios/{apto}")).json()
    assert d["tamizaje"]["estado_tamizaje"] == "apto_tamizaje"
    assert set(d["tamizaje"]["reglas"]) == {"T01_territorio", "T02_acceso_osm", "T03_trazabilidad_documental",
                                            "T04_datos_secundarios", "T05_sin_punto_existente_30m"}
    assert set(d["criterios_mcda"]) == {"K1_demanda", "K2_generacion", "K3_brecha", "K4_accesibilidad"}
    assert [x["esquema"] for x in d["mcda"]][0] == "iguales" and len(d["mcda"]) == 3
    assert d["montecarlo"]["mc_frecuencia_top_k"] is not None
    assert len(d["variables"]) == 14 and d["variables"]["pob_wp_r500"]["unidad"] == "personas"
    assert set(d["propension"]["contribuciones_logit"]) == {"clase", "poblacion", "vias", "pois", "construido",
                                                            "pendiente", "sigersol"}
    assert "no es probabilidad" in d["propension"]["aviso"].lower()
    _atribucion_ok(d)

    positivo = t.loc[t["estado_tamizaje"] == "punto_existente", "sitio_id"].iloc[0]
    d = (await _get(client, admin_token, f"/sitios/{positivo}")).json()
    assert d["rol"] == "positivo" and d["criterios_mcda"] is None and d["mcda"] == [] and d["montecarlo"] is None
    assert (await _get(client, admin_token, "/sitios/NO-EXISTE")).status_code == 404


async def test_tamizaje_y_mcda(client, admin_token):
    t = (await _get(client, admin_token, "/tamizaje")).json()
    assert t["estado_tamizaje"] == {"apto_tamizaje": 2192, "excluido_tamizaje": 3, "punto_existente": 47}
    assert t["reglas"]["T05_sin_punto_existente_30m"]["no_cumple"] == 3
    assert t["exclusiones"][0]["codigo_exclusion"] == "T05" and t["exclusiones"][0]["n"] == 3
    _atribucion_ok(t)

    m = (await _get(client, admin_token, "/mcda")).json()
    assert m["mcda_version"] == "1.0.2" and m["top_k"] == 110 and m["esquema_principal"] == "iguales"
    assert [p["esquema"] for p in m["pesos"]][0] == "iguales" and len(m["pesos"]) == 3
    assert all(abs(sum(v for k, v in p.items() if k != "esquema") - 1) < 1e-9 for p in m["pesos"])
    assert set(m["criterios"]) == {"K1_demanda", "K2_generacion", "K3_brecha", "K4_accesibilidad"}
    assert len(m["concordancia"]) == 8 and m["montecarlo"]["n"] == 5000


async def test_distritos_geojson(client, admin_token):
    r = await _get(client, admin_token, "/distritos")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/geo+json")
    g = r.json()
    assert len(g["features"]) == 43 and {f["geometry"]["type"] for f in g["features"]} == {"MultiPolygon"}
    props = [f["properties"] for f in g["features"]]
    assert sum(p["n_sitios"] for p in props) == 2242 and sum(p["n_top_k"] for p in props) == 110
    assert sum(p["n_apto_tamizaje"] for p in props) == 2192 and sum(p["n_punto_existente"] for p in props) == 47
    _atribucion_ok(g)
    sin_geom = (await _get(client, admin_token, "/distritos", geometria="false")).json()
    assert all(f["geometry"] is None for f in sin_geom["features"])
    simple = await _get(client, admin_token, "/distritos", simplificar=0.0005)
    assert len(simple.content) < len(r.content)
    assert (await _get(client, admin_token, "/distritos", simplificar=1)).status_code == 422


async def test_fuentes_y_atribucion(client, admin_token):
    r = await _get(client, admin_token, "/fuentes")
    f = r.json()["fuentes"]
    assert len(f) == 11 and {x["licencia_estado"] for x in f} <= {"verificada", "sin_licencia_explicita"}
    assert all(x["atribucion"] and x["licencia"] and len(x["sha256"]) == 64 for x in f)
    assert any("OpenStreetMap" in x["atribucion"] for x in f)
    _atribucion_ok(r.json())


async def test_modelo_oficial_logistico(client, admin_token):
    r = await _get(client, admin_token, "/modelo")
    m = r.json()
    assert m["modelo_version"] == "propension-1.0.0" and m["algoritmo"].startswith("LogisticRegression")
    assert len(m["metricas_por_semilla"]) == 10 and "roc_auc" in m["metricas_resumen"]
    assert "responsable" not in m["model_card"] and "probabilidad" in m["aviso"]
    _atribucion_ok(m)


async def test_ninguna_respuesta_usa_categorias_demo(client, admin_token):
    """La V1 solo tiene puntajes y rangos: ninguna respuesta inventa Alta/Media/Baja."""
    for ruta in ("/versiones/activa", "/sitios", "/sitios/geojson", "/tamizaje", "/mcda", "/distritos", "/modelo"):
        texto = (await _get(client, admin_token, ruta)).text
        assert not re.search(r'"(Alta|Media|Baja)"', texto), ruta
        assert "priority_label" not in texto and "ml_score" not in texto


async def test_endpoints_demo_siguen_respondiendo(client, admin_token):
    for ruta in ("/api/v1/ml/recommendations", "/api/v1/map/districts", "/api/v1/map/points"):
        assert (await client.get(ruta, headers=auth_headers(admin_token))).status_code == 200, ruta
