"""Consultas de solo lectura sobre los resultados importados (esquema `analisis`).

Devuelven lo que trae el paquete, sin recalcular la metodología:
- tamizaje: `apto_tamizaje` significa que el sitio pasa las reglas evaluables con
  datos secundarios; no confirma viabilidad (la verificación de campo está pendiente);
- propensión: se expone su percentil como contexto; no es probabilidad de éxito
  ni idoneidad y no entra en el puntaje MCDA;
- MCDA: puntaje y rango por esquema (`iguales` es el principal); el top-k es
  «prioridad MCDA», no una recomendación final. No hay categorías Alta/Media/Baja.

Cada respuesta lleva la atribución y la licencia de los datos (ODbL 1.0).
"""

import json
from dataclasses import dataclass
from datetime import timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analisis import (
    CONTRIBUCIONES,
    CRITERIOS_CAMPO,
    FEATURES,
    REGLAS_TAMIZAJE,
    SCHEMA,
    VersionAnalisis,
)

ESQUEMA_PRINCIPAL = "iguales"
LICENCIAS_URL = {"ODbL-1.0": "https://opendatacommons.org/licenses/odbl/1-0/"}
AVISO_DATOS = (
    "Datos: © colaboradores de OpenStreetMap (ODbL) · WorldPop (CC BY 4.0) · GHSL, JRC (CC BY 4.0) · "
    "Copernicus DEM · MINAM-SIGERSOL e IGN/INEI (ODC-By) · Municipalidad de San Isidro."
)
AVISOS = {
    "tamizaje": "apto_tamizaje: el sitio pasa las reglas evaluables con datos secundarios. No confirma viabilidad; "
                "la verificación en campo (C02–C09) está pendiente.",
    "propension": "Propensión de emplazamiento: parecido del sitio con los lugares donde ya existen puntos de "
                  "reciclaje (regresión logística, fuera de fold). No es probabilidad de éxito, idoneidad ni "
                  "viabilidad; se muestra como percentil y no entra en el puntaje MCDA.",
    "mcda": "La MCDA ordena oportunidades con los criterios K1–K4; el top-k es «prioridad MCDA», no una "
            "recomendación final.",
}
_CRITERIOS = ("k1_demanda", "k2_generacion", "k3_brecha", "k4_accesibilidad")
_RESULTADOS_MAX = 5000
# Fechas en la zona del contrato (America/Lima, sin horario de verano).
_LIMA = timezone(timedelta(hours=-5))


class VersionNoEncontrada(LookupError):
    pass


def atribucion(version: VersionAnalisis) -> dict:
    return {
        "licencia": version.licencia_datos,
        "licencia_url": LICENCIAS_URL.get(version.licencia_datos),
        "aviso": AVISO_DATOS,
        "fuentes": "/api/v1/analisis/fuentes",
        "paquete_version": version.paquete_version,
    }


def _version_resumen(v: VersionAnalisis) -> dict:
    return {
        "paquete_version": v.paquete_version,
        "dataset_version": v.dataset_version,
        "modelo_version": v.modelo_version,
        "mcda_version": v.mcda_version,
        "fecha_paquete": v.fecha_paquete.astimezone(_LIMA).isoformat(),
        "descripcion": v.descripcion,
        "licencia_datos": v.licencia_datos,
        "activa": v.activa,
        "importado_en": v.importado_en.astimezone(_LIMA).isoformat(),
        "manifest_sha256": v.manifest_sha256,
    }


async def resolver_version(db: AsyncSession, version: str | None) -> VersionAnalisis:
    """La versión pedida (`paquete_version`) o, si no se indica, la activa."""
    q = select(VersionAnalisis)
    q = q.where(VersionAnalisis.activa.is_(True)) if version is None else q.where(
        VersionAnalisis.paquete_version == version)
    v = (await db.execute(q)).scalar_one_or_none()
    if v is None:
        raise VersionNoEncontrada(
            "no hay una versión de análisis activa" if version is None else f"la versión {version} no está importada")
    return v


async def listar_versiones(db: AsyncSession) -> list[dict]:
    vs = (await db.execute(select(VersionAnalisis).order_by(VersionAnalisis.version_id))).scalars().all()
    return [_version_resumen(v) for v in vs]


async def detalle_version(db: AsyncSession, v: VersionAnalisis) -> dict:
    m = v.manifest
    conteos = (await db.execute(text(f"""
        SELECT count(*) AS sitios,
               count(*) FILTER (WHERE s.rol = 'positivo') AS positivos,
               count(*) FILTER (WHERE s.rol = 'fondo') AS fondo,
               count(DISTINCT s.ubigeo) AS distritos_con_sitios,
               (SELECT count(*) FROM {SCHEMA}.distrito d WHERE d.version_id = :v) AS distritos
        FROM {SCHEMA}.sitio s WHERE s.version_id = :v"""), {"v": v.version_id})).one()._asdict()
    estados = await _contar(db, "tamizaje", "estado_tamizaje", v.version_id)
    ds, mo, mc = m["dataset"], m["modelo"], m["mcda"]
    return {
        **_version_resumen(v),
        "conteos": {**conteos, "estado_tamizaje": estados},
        "dataset": {k: ds.get(k) for k in ("version", "archivo", "sha256", "filas", "positivos", "fondo", "distritos",
                                           "distritos_con_positivos", "unidades")},
        "modelo": {k: mo.get(k) for k in ("version", "algoritmo", "cv", "semillas", "features")},
        "mcda": {k: mc.get(k) for k in ("version", "metodo", "radio_principal_m", "top_k", "top_k_fraccion",
                                        "montecarlo_n", "montecarlo_semilla")} | {"esquema_principal":
                                                                                   ESQUEMA_PRINCIPAL},
        "entorno": m.get("entorno"),
        "archivos": [{k: a.get(k) for k in ("ruta", "sha256", "bytes", "filas")} for a in m.get("archivos", [])],
        "avisos": AVISOS,
    }


async def _contar(db: AsyncSession, tabla: str, columna: str, version_id: int) -> dict:
    """Conteo por valor de una columna fija (nombres internos, nunca de la petición)."""
    filas = await db.execute(text(
        f"SELECT {columna} AS valor, count(*) AS n FROM {SCHEMA}.{tabla} WHERE version_id = :v "
        f"GROUP BY 1 ORDER BY 1 NULLS LAST"), {"v": version_id})
    return {("sin_dato" if r.valor is None else r.valor): r.n for r in filas}


# ----------------------------------------------------------------------------- sitios
@dataclass
class FiltrosSitios:
    estado_tamizaje: str | None = None
    rol: str | None = None
    clase_oportunidad: str | None = None
    ubigeo: str | None = None
    en_top_k: bool | None = None
    bbox: tuple[float, float, float, float] | None = None  # min_lon, min_lat, max_lon, max_lat


# La distancia al punto existente (tamizaje) y los criterios K1–K4 (criterio_mcda) vienen tal cual del paquete;
# se incluyen en la lista para que Reportes y la exportación no tengan que pedir el detalle sitio por sitio.
_SELECT_SITIOS = f"""
    SELECT s.sitio_id, s.lon, s.lat, s.ubigeo, s.distrito, s.clase_oportunidad, s.rol,
           t.estado_tamizaje, t.codigo_exclusion, t.dist_punto_existente_m, t.punto_existente_mas_cercano,
           p.percentil_oof,
           m.puntaje, m.rango, m.en_top_k, mc.mc_frecuencia_top_k,
           {", ".join(f"c.{k}_bruto, c.{k}_norm" for k in _CRITERIOS)}
    FROM {SCHEMA}.sitio s
    JOIN {SCHEMA}.tamizaje t ON t.version_id = s.version_id AND t.sitio_id = s.sitio_id
    JOIN {SCHEMA}.propension p ON p.version_id = s.version_id AND p.sitio_id = s.sitio_id
    LEFT JOIN {SCHEMA}.mcda m ON m.version_id = s.version_id AND m.sitio_id = s.sitio_id AND m.esquema = :esquema
    LEFT JOIN {SCHEMA}.mcda_montecarlo mc ON mc.version_id = s.version_id AND mc.sitio_id = s.sitio_id
    LEFT JOIN {SCHEMA}.criterio_mcda c ON c.version_id = s.version_id AND c.sitio_id = s.sitio_id
"""
_ORDEN = {"rango": "m.rango ASC NULLS LAST, s.sitio_id", "sitio_id": "s.sitio_id"}


def _where(filtros: FiltrosSitios, version_id: int, esquema: str) -> tuple[str, dict]:
    cond, params = ["s.version_id = :v"], {"v": version_id, "esquema": esquema}
    for campo, col in (("estado_tamizaje", "t.estado_tamizaje"), ("rol", "s.rol"),
                       ("clase_oportunidad", "s.clase_oportunidad"), ("ubigeo", "s.ubigeo")):
        valor = getattr(filtros, campo)
        if valor is not None:
            cond.append(f"{col} = :{campo}")
            params[campo] = valor
    if filtros.en_top_k is not None:
        cond.append("m.en_top_k = :en_top_k")
        params["en_top_k"] = filtros.en_top_k
    if filtros.bbox is not None:
        cond.append("s.lon BETWEEN :min_lon AND :max_lon AND s.lat BETWEEN :min_lat AND :max_lat")
        params.update(zip(("min_lon", "min_lat", "max_lon", "max_lat"), filtros.bbox))
    return " AND ".join(cond), params


def _criterios(fila) -> dict | None:
    """K1–K4 (bruto y normalizado) con las mismas claves que el detalle; None si el sitio no tiene MCDA."""
    if fila["k1_demanda_norm"] is None:
        return None
    return {k.upper()[:2] + k[2:]: {"bruto": fila[f"{k}_bruto"], "norm": fila[f"{k}_norm"]} for k in _CRITERIOS}


def _sitio_resumen(r) -> dict:
    return {
        "sitio_id": r.sitio_id,
        "lon": r.lon,
        "lat": r.lat,
        "ubigeo": r.ubigeo,
        "distrito": r.distrito,
        "clase_oportunidad": r.clase_oportunidad,
        "rol": r.rol,
        "estado_tamizaje": r.estado_tamizaje,
        "codigo_exclusion": r.codigo_exclusion,
        "dist_punto_existente_m": r.dist_punto_existente_m,
        "punto_existente_mas_cercano": r.punto_existente_mas_cercano,
        # Contexto: percentil de la propensión fuera de fold (no es probabilidad de éxito).
        "propension_percentil": r.percentil_oof,
        # Solo los sitios apto_tamizaje tienen MCDA.
        "mcda": None if r.rango is None else {"puntaje": r.puntaje, "rango": r.rango, "en_top_k": r.en_top_k},
        "mc_frecuencia_top_k": r.mc_frecuencia_top_k,
        "criterios_mcda": _criterios(r._mapping),
    }


async def listar_sitios(db: AsyncSession, version_id: int, filtros: FiltrosSitios, esquema: str, orden: str,
                        limit: int, offset: int) -> tuple[int, list[dict]]:
    where, params = _where(filtros, version_id, esquema)
    total = (await db.execute(text(f"SELECT count(*) FROM ({_SELECT_SITIOS} WHERE {where}) x"), params)).scalar_one()
    filas = await db.execute(
        text(f"{_SELECT_SITIOS} WHERE {where} ORDER BY {_ORDEN[orden]} LIMIT :limit OFFSET :offset"),
        params | {"limit": limit, "offset": offset})
    return total, [_sitio_resumen(r) for r in filas]


async def sitios_geojson(db: AsyncSession, version_id: int, filtros: FiltrosSitios, esquema: str) -> dict:
    where, params = _where(filtros, version_id, esquema)
    filas = await db.execute(
        text(f"{_SELECT_SITIOS} WHERE {where} ORDER BY {_ORDEN['rango']} LIMIT {_RESULTADOS_MAX}"), params)
    features = []
    for r in filas:
        props = _sitio_resumen(r)
        lon, lat = props.pop("lon"), props.pop("lat")
        features.append({"type": "Feature", "id": r.sitio_id,
                         "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props})
    return {"type": "FeatureCollection", "features": features}


async def detalle_sitio(db: AsyncSession, v: VersionAnalisis, sitio_id: str) -> dict | None:
    p = {"v": v.version_id, "s": sitio_id}
    base = (await db.execute(text(f"""
        SELECT *  -- con USING, version_id y sitio_id salen una sola vez
        FROM {SCHEMA}.sitio s
        JOIN {SCHEMA}.tamizaje t USING (version_id, sitio_id)
        JOIN {SCHEMA}.sitio_feature f USING (version_id, sitio_id)
        JOIN {SCHEMA}.propension pr USING (version_id, sitio_id)
        LEFT JOIN {SCHEMA}.criterio_mcda c USING (version_id, sitio_id)
        LEFT JOIN {SCHEMA}.mcda_montecarlo mc USING (version_id, sitio_id)
        WHERE version_id = :v AND sitio_id = :s"""), p)).mappings().one_or_none()
    if base is None:
        return None
    mcda = (await db.execute(text(
        f"SELECT esquema, puntaje, rango, en_top_k, mcda_version FROM {SCHEMA}.mcda "
        f"WHERE version_id = :v AND sitio_id = :s ORDER BY esquema = :principal DESC, esquema"),
        p | {"principal": ESQUEMA_PRINCIPAL})).mappings().all()
    unidades = v.manifest.get("dataset", {}).get("unidades", {})
    reglas = {r.upper()[:3] + r[3:]: base[r] for r in REGLAS_TAMIZAJE}  # cabecera original (T01_territorio)
    return {
        "sitio_id": base["sitio_id"],
        "lon": base["lon"],
        "lat": base["lat"],
        "ubigeo": base["ubigeo"],
        "distrito": base["distrito"],
        "clase_oportunidad": base["clase_oportunidad"],
        "rol": base["rol"],
        "tamizaje": {
            "estado_tamizaje": base["estado_tamizaje"],
            "reglas": reglas,
            "dist_punto_existente_m": base["dist_punto_existente_m"],
            "punto_existente_mas_cercano": base["punto_existente_mas_cercano"],
            "codigo_exclusion": base["codigo_exclusion"],
            "motivo_exclusion": base["motivo_exclusion"],
            "controles_campo": {c.upper(): base[c] for c in CRITERIOS_CAMPO},
            "estado_verificacion_campo": base["estado_verificacion_campo"],
        },
        "variables": {f: {"valor": base[f], "unidad": unidades.get(f)} for f in FEATURES},
        "propension": {
            "percentil_oof": base["percentil_oof"],
            "propension_oof": base["propension_oof"],
            "propension_oof_de": base["propension_oof_de"],
            "percentil_final": base["percentil_final"],
            "propension_final": base["propension_final"],
            "contribuciones_logit": {c.removeprefix("contrib_"): base[c] for c in CONTRIBUCIONES},
            "aviso": AVISOS["propension"],
        },
        "criterios_mcda": _criterios(base),
        "mcda": [dict(r) for r in mcda],
        "montecarlo": None if base["mc_frecuencia_top_k"] is None else {
            k: base[k] for k in ("mc_rango_mediana", "mc_rango_p05", "mc_rango_p95", "mc_frecuencia_top_k")},
        "avisos": AVISOS,
    }


# ----------------------------------------------------------------------------- tamizaje y MCDA
async def resumen_tamizaje(db: AsyncSession, version_id: int) -> dict:
    reglas = {}
    for r in REGLAS_TAMIZAJE:
        reglas[r.upper()[:3] + r[3:]] = await _contar(db, "tamizaje", r, version_id)
    exclusiones = await db.execute(text(
        f"SELECT codigo_exclusion, motivo_exclusion, count(*) AS n FROM {SCHEMA}.tamizaje "
        f"WHERE version_id = :v AND estado_tamizaje = 'excluido_tamizaje' GROUP BY 1, 2 ORDER BY 1, 2"),
        {"v": version_id})
    return {
        "estado_tamizaje": await _contar(db, "tamizaje", "estado_tamizaje", version_id),
        "reglas": reglas,
        "exclusiones": [dict(r._mapping) for r in exclusiones],
        "estado_verificacion_campo": await _contar(db, "tamizaje", "estado_verificacion_campo", version_id),
        "aviso": AVISOS["tamizaje"],
    }


async def resumen_mcda(db: AsyncSession, v: VersionAnalisis) -> dict:
    mc = v.manifest.get("mcda", {})
    pesos = await db.execute(text(
        f"SELECT esquema, {', '.join(_CRITERIOS)} FROM {SCHEMA}.peso_mcda WHERE version_id = :v "
        f"ORDER BY esquema = :principal DESC, esquema"), {"v": v.version_id, "principal": ESQUEMA_PRINCIPAL})
    concordancia = await db.execute(text(
        f"SELECT comparacion, spearman, kendall_tau_b, solapamiento_top110 FROM {SCHEMA}.concordancia_mcda "
        f"WHERE version_id = :v ORDER BY comparacion"), {"v": v.version_id})
    return {
        "mcda_version": v.mcda_version,
        "metodo": mc.get("metodo"),
        "criterios": mc.get("criterios"),
        "contexto": mc.get("contexto"),
        "radio_principal_m": mc.get("radio_principal_m"),
        "top_k": mc.get("top_k"),
        "top_k_fraccion": mc.get("top_k_fraccion"),
        "montecarlo": {"n": mc.get("montecarlo_n"), "semilla": mc.get("montecarlo_semilla")},
        "esquema_principal": ESQUEMA_PRINCIPAL,
        "pesos": [{"esquema": r.esquema, **{k.upper()[:2] + k[2:]: getattr(r, k) for k in _CRITERIOS}}
                  for r in pesos],
        # Informativa: sensibilidad del ranking; no se usa para priorizar.
        "concordancia": [dict(r._mapping) for r in concordancia],
        "historial": mc.get("historial"),
        "aviso": AVISOS["mcda"],
    }


# ----------------------------------------------------------------------------- distritos, fuentes y modelo
async def distritos_geojson(db: AsyncSession, version_id: int, esquema: str, geometria: bool,
                            simplificar: float | None) -> dict:
    geom = ("NULL" if not geometria else
            "ST_AsGeoJSON(ST_SimplifyPreserveTopology(d.geometry, :tol), 7)" if simplificar else
            "ST_AsGeoJSON(d.geometry, 7)")
    filas = await db.execute(text(f"""
        WITH c AS (
            SELECT s.ubigeo,
                   count(*) AS n_sitios,
                   count(*) FILTER (WHERE t.estado_tamizaje = 'apto_tamizaje') AS n_apto_tamizaje,
                   count(*) FILTER (WHERE t.estado_tamizaje = 'excluido_tamizaje') AS n_excluido_tamizaje,
                   count(*) FILTER (WHERE t.estado_tamizaje = 'punto_existente') AS n_punto_existente,
                   count(*) FILTER (WHERE m.en_top_k) AS n_top_k
            FROM {SCHEMA}.sitio s
            JOIN {SCHEMA}.tamizaje t ON t.version_id = s.version_id AND t.sitio_id = s.sitio_id
            LEFT JOIN {SCHEMA}.mcda m ON m.version_id = s.version_id AND m.sitio_id = s.sitio_id
                                      AND m.esquema = :esquema
            WHERE s.version_id = :v GROUP BY s.ubigeo)
        SELECT d.ubigeo, d.nombre, {geom} AS geojson,
               coalesce(c.n_sitios, 0) AS n_sitios, coalesce(c.n_apto_tamizaje, 0) AS n_apto_tamizaje,
               coalesce(c.n_excluido_tamizaje, 0) AS n_excluido_tamizaje,
               coalesce(c.n_punto_existente, 0) AS n_punto_existente, coalesce(c.n_top_k, 0) AS n_top_k
        FROM {SCHEMA}.distrito d LEFT JOIN c USING (ubigeo)
        WHERE d.version_id = :v ORDER BY d.ubigeo"""),
        {"v": version_id, "esquema": esquema} | ({"tol": simplificar} if geometria and simplificar else {}))
    features = []
    for r in filas:
        props = {k: v for k, v in r._mapping.items() if k != "geojson"}
        features.append({"type": "Feature", "id": r.ubigeo,
                         "geometry": json.loads(r.geojson) if r.geojson else None, "properties": props})
    return {"type": "FeatureCollection", "esquema": esquema, "features": features}


async def listar_fuentes(db: AsyncSession, version_id: int) -> list[dict]:
    filas = await db.execute(text(
        f"SELECT codigo, tipo, nombre, institucion, url_descarga, url_pagina, licencia, licencia_url, "
        f"licencia_estado, licencia_verificada_en, atribucion, fecha_corte, sha256, bytes, uso_en_v1 "
        f"FROM {SCHEMA}.fuente WHERE version_id = :v ORDER BY codigo"), {"v": version_id})
    return [{**r._mapping, "licencia_verificada_en": r.licencia_verificada_en.isoformat()} for r in filas]


async def modelo(db: AsyncSession, version_id: int) -> dict | None:
    fila = (await db.execute(text(
        f"SELECT modelo_version, algoritmo, model_card, parametros, metricas_resumen FROM {SCHEMA}.modelo "
        f"WHERE version_id = :v"), {"v": version_id})).one_or_none()
    if fila is None:
        return None
    semillas = await db.execute(text(
        f"SELECT semilla, roc_auc, pr_auc, boyce, prevalencia, recall_top5, recall_top10, recall_top20 "
        f"FROM {SCHEMA}.metrica_semilla WHERE version_id = :v ORDER BY semilla"), {"v": version_id})
    # La ficha se sirve completa salvo el nombre de la persona responsable.
    card = {k: v for k, v in fila.model_card.items() if k != "responsable"}
    return {
        "modelo_version": fila.modelo_version,
        "algoritmo": fila.algoritmo,
        "rol": "modelo oficial de propensión (regresión logística); LightGBM figura solo como comparación",
        "metricas_resumen": fila.metricas_resumen,
        "metricas_por_semilla": [dict(r._mapping) for r in semillas],
        "model_card": card,
        "parametros": fila.parametros,
        "aviso": AVISOS["propension"],
    }
