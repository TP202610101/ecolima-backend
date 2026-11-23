"""Lectura y validación del paquete de resultados versionado (contrato del paquete v1).

El paquete se calcula fuera del backend (repositorio `ecolima-ml`) y llega como
un directorio con `manifest.json`. Antes de cargar nada, el importador ejecuta:

- la validación 0 de este módulo: manifiesto completo, versiones soportadas y
  modelo oficial (regresión logística; LightGBM es solo comparación);
- las validaciones 1–15 del contrato (§6): integridad, esquema, coherencia
  entre archivos, coherencia numérica y territorio.

Si alguna falla, el paquete completo se rechaza. Las validaciones 1–15 son una
copia de las del paquete científico, adaptada para no depender de él.

Uso:
    from app.services.analisis_paquete import validar
    informe = validar(ruta)        # informe.ok, informe.fallos, informe.lector
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------- versiones soportadas
# El esquema `analisis` sigue la versión mayor 1 del contrato del paquete.
PAQUETE_MAYOR_SOPORTADA = 1
# Modelo oficial de propensión: regresión logística. Un paquete cuyo modelo
# principal sea otro (p. ej. LightGBM, que solo se usa como comparación) se rechaza.
ALGORITMO_OFICIAL = "LogisticRegression"
PREFIJO_MODELO = "propension-"

# ----------------------------------------------------------------------------- esquema (contrato §4)
RADIOS = (250, 500, 1000)
VARIABLES = [f"{v}_r{r}" for v in ("pob_wp", "vias_km_km2", "poi_n_km2", "ghsl_frac_construida") for r in RADIOS] + [
    "pendiente_grados", "sigersol_gpc_municipal_kg_hab_dia"]
CRITERIOS = ["K1_demanda", "K2_generacion", "K3_brecha", "K4_accesibilidad"]
GRUPOS_CONTRIB = ["clase", "poblacion", "vias", "pois", "construido", "pendiente", "sigersol"]
REGLAS_T = ["T01_territorio", "T02_acceso_osm", "T03_trazabilidad_documental", "T04_datos_secundarios",
            "T05_sin_punto_existente_30m"]
CONTROLES_C = [f"C0{i}" for i in range(2, 10)]
# Columnas de tamizaje.csv que solo pueden estar vacías en los sitios punto_existente.
NULOS_SOLO_PUNTO_EXISTENTE = [*REGLAS_T, "dist_punto_existente_m", "punto_existente_mas_cercano", *CONTROLES_C,
                              "estado_verificacion_campo"]
ESQUEMAS = ("iguales", "critic", "entropia")
CLASES = ("paradero_transporte_publico", "mercado_municipal_no_confirmado", "centro_comercial", "parque")

# archivo -> (columnas en orden con su tipo, columnas que admiten nulos)
# tipos: t = texto, r = real, i = entero, b = booleano True/False, d = fecha YYYY-MM-DD
COLUMNAS: dict[str, tuple[list[tuple[str, str]], set[str]]] = {
    "salidas/sitios.csv": ([("sitio_id", "t"), ("lon", "r"), ("lat", "r"), ("ubigeo", "i"), ("distrito", "t"),
                            ("clase_oportunidad", "t"), ("rol", "t")], set()),
    "salidas/features.csv": ([("sitio_id", "t"), ("clase_oportunidad", "t"), *[(v, "r") for v in VARIABLES]], set()),
    # Orden real del archivo: T05 va después de las columnas de distancia.
    "salidas/tamizaje.csv": ([("sitio_id", "t"), *[(t, "t") for t in REGLAS_T[:4]], ("dist_punto_existente_m", "r"),
                              ("punto_existente_mas_cercano", "t"), (REGLAS_T[4], "t"), ("codigo_exclusion", "t"),
                              ("motivo_exclusion", "t"), ("estado_tamizaje", "t"), *[(c, "t") for c in CONTROLES_C],
                              ("estado_verificacion_campo", "t")],
                             {*NULOS_SOLO_PUNTO_EXISTENTE, "codigo_exclusion", "motivo_exclusion"}),
    "salidas/propension.csv": ([("sitio_id", "t"), ("rol", "t"), ("propension_oof", "r"), ("propension_oof_de", "r"),
                                ("percentil_oof", "r"), ("propension_final", "r"), ("percentil_final", "r"),
                                *[(f"contrib_{g}", "r") for g in GRUPOS_CONTRIB]], set()),
    "salidas/propension_oof_por_semilla.csv": ([("sitio_id", "t"), *[(f"semilla_{i}", "r") for i in range(10)]], set()),
    "salidas/criterios_mcda.csv": ([("sitio_id", "t"),
                                    *[(f"{k}_{s}", "r") for k in CRITERIOS for s in ("bruto", "norm")],
                                    ("contexto_propension_oof", "r")], set()),
    "salidas/pesos_mcda.csv": ([("esquema", "t"), *[(k, "r") for k in CRITERIOS]], set()),
    "salidas/mcda.csv": ([("mcda_version", "t"), ("sitio_id", "t"), ("esquema", "t"), ("puntaje", "r"), ("rango", "i"),
                          ("en_top_k", "b")], set()),
    "salidas/mcda_montecarlo.csv": ([("mcda_version", "t"), ("sitio_id", "t"), ("mc_rango_mediana", "r"),
                                     ("mc_rango_p05", "r"), ("mc_rango_p95", "r"), ("mc_frecuencia_top_k", "r")],
                                    set()),
    "salidas/concordancia_mcda.csv": ([("comparacion", "t"), ("spearman", "r"), ("kendall_tau_b", "r"),
                                       ("solapamiento_top110", "r")], set()),
    "datos/fuentes.csv": ([("codigo", "t"), ("tipo", "t"), ("nombre", "t"), ("institucion", "t"), ("url_descarga", "t"),
                           ("url_pagina", "t"), ("licencia", "t"), ("licencia_url", "t"), ("licencia_estado", "t"),
                           ("licencia_verificada_en", "d"), ("atribucion", "t"), ("fecha_corte", "t"), ("sha256", "t"),
                           ("bytes", "i"), ("uso_en_v1", "t")],
                          {"url_descarga", "url_pagina", "licencia_url", "licencia_verificada_en"}),
    "modelo/metricas_por_semilla.csv": ([("semilla", "i"), *[(m, "r") for m in (
        "roc_auc", "pr_auc", "boyce", "prevalencia", "recall_top5", "recall_top10", "recall_top20")]], set()),
}
ENUMERACIONES: dict[str, dict[str, set[str]]] = {
    "salidas/sitios.csv": {"rol": {"positivo", "fondo"}, "clase_oportunidad": set(CLASES)},
    "salidas/features.csv": {"clase_oportunidad": set(CLASES)},
    "salidas/propension.csv": {"rol": {"positivo", "fondo"}},
    "salidas/tamizaje.csv": {**{t: {"cumple", "cumple_parcial", "no_cumple"} for t in REGLAS_T},
                             "estado_tamizaje": {"apto_tamizaje", "excluido_tamizaje", "punto_existente"},
                             "codigo_exclusion": {"T05"},
                             **{c: {"pendiente_campo"} for c in CONTROLES_C},
                             "estado_verificacion_campo": {"pendiente_campo"}},
    "salidas/mcda.csv": {"esquema": set(ESQUEMAS)},
    "salidas/pesos_mcda.csv": {"esquema": set(ESQUEMAS)},
    "datos/fuentes.csv": {"tipo": {"primaria", "derivada"},
                          "licencia_estado": {"verificada", "sin_licencia_explicita", "no_verificada"}},
}
PATRON_SITIO = re.compile(r"^(ECO-V0\.1-OSM-NODE-\d+|POS-OSM-N\d+|POS-SI-ER\d\d)$")
RECUADRO = {"lon": (-77.20, -76.60), "lat": (-12.55, -11.55)}
UBIGEOS = set(range(150101, 150144))  # 43 distritos de la provincia de Lima
TOL_FEATURES, TOL_SEMILLAS, TOL_PERCENTIL, TOL_LOGIT, TOL_MCDA = 1e-12, 1e-12, 1e-12, 1e-9, 1e-9

REGLAS = {
    0: "Manifiesto: claves obligatorias, versión mayor soportada y modelo oficial (regresión logística)",
    1: "Integridad: sha256 y bytes de cada archivo iguales al manifiesto; ningún archivo sin listar",
    2: "Integridad: mcda_version de mcda.csv y mcda_montecarlo.csv igual a manifest.mcda.version",
    3: "Integridad: ninguna fuente no_verificada; cada fila cumple las condiciones de su licencia_estado",
    4: "Esquema: columnas exactas y en orden, tipos y nulos (incluye distritos.geojson)",
    5: "Esquema: valores dentro de sus enumeraciones; ninguna celda ni columna «viable»",
    6: "Coherencia: sitio_id único y el mismo conjunto en sitios, features, tamizaje, propension y "
       "propension_oof_por_semilla",
    7: "Coherencia: rol igual en sitios y propension; punto_existente ⇔ positivo",
    8: "Coherencia: sitios de criterios_mcda, mcda (por esquema) y mcda_montecarlo = apto_tamizaje; mcda = 3 × n",
    9: "Coherencia: features.csv y sitios.csv coinciden con el dataset congelado (rtol 1e-12)",
    10: "Numérica: propension_oof = media de semilla_0…9; propension_oof_de = desviación muestral (1e-12)",
    11: "Numérica: percentil_* = rango promedio / n (1e-12)",
    12: "Numérica: |logit(propension_final) − logit_base − Σ contrib_*| < 1e-9",
    13: "Numérica: pesos suman 1; puntaje = Σ peso × K_norm; rango ordena y es permutación; en_top_k ⇔ rango ≤ top_k",
    14: "Numérica: K*_norm en [0, 1]; contexto_propension_oof = propension_oof",
    15: "Territorio: coordenadas en el recuadro; ubigeo y nombre dentro de los 43 distritos de distritos.geojson",
}


class ErrorPaquete(Exception):
    """El paquete no existe, no es compatible o no supera las validaciones."""

    def __init__(self, mensaje: str, fallos: list[Resultado] | None = None):
        super().__init__(mensaje)
        self.fallos = fallos or []


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


class Paquete:
    """Directorio del paquete con su manifiesto."""

    def __init__(self, ruta: Path | str):
        self.ruta = Path(ruta)
        archivo = self.ruta / "manifest.json"
        if not archivo.is_file():
            raise ErrorPaquete("el directorio no contiene manifest.json")
        self.manifest_bytes = archivo.read_bytes()
        try:
            self.manifest = json.loads(self.manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as ex:
            raise ErrorPaquete(f"manifest.json no es JSON UTF-8 válido: {ex}") from ex
        if not isinstance(self.manifest, dict):
            raise ErrorPaquete("manifest.json no es un objeto JSON")
        self.manifest_sha256 = hashlib.sha256(self.manifest_bytes).hexdigest()

    def errores_hash(self) -> list[str]:
        """sha256 y bytes de cada archivo frente al manifiesto; ningún archivo sin listar (validación 1)."""
        listados = {a["ruta"]: a for a in self.manifest["archivos"]}
        reales = {p.relative_to(self.ruta).as_posix() for p in self.ruta.rglob("*") if p.is_file()} - {"manifest.json"}
        e = [f"sin listar en el manifiesto: {r}" for r in sorted(reales - set(listados))]
        e += [f"listado pero ausente: {r}" for r in sorted(set(listados) - reales)]
        for r in sorted(reales & set(listados)):
            if (self.ruta / r).stat().st_size != listados[r]["bytes"]:
                e.append(f"bytes distinto: {r}")
            elif sha256_archivo(self.ruta / r) != listados[r]["sha256"]:
                e.append(f"sha256 distinto: {r}")
        return e

    def archivo(self, ruta: str) -> Path:
        return self.ruta / ruta

    def json(self, ruta: str) -> dict:
        return json.loads(self.archivo(ruta).read_text(encoding="utf-8"))

    def dataset(self) -> pd.DataFrame:
        """Dataset congelado; ubigeo como texto."""
        return pd.read_csv(self.archivo(self.manifest["dataset"]["archivo"]), dtype={"ubigeo": str})


@dataclass
class Resultado:
    n: int
    regla: str
    errores: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores


class Lector:
    """Lee cada CSV una vez: como texto (para esquema, enumeraciones y carga) y con tipos (para cálculos)."""

    def __init__(self, paquete: Paquete):
        self.p = paquete
        self._txt: dict[str, pd.DataFrame] = {}

    def txt(self, ruta: str) -> pd.DataFrame:
        if ruta not in self._txt:
            self._txt[ruta] = pd.read_csv(self.p.archivo(ruta), dtype=str, keep_default_na=False, encoding="utf-8")
        return self._txt[ruta]

    def num(self, ruta: str, indice: str | None = "sitio_id") -> pd.DataFrame:
        df = self.txt(ruta).copy()
        for col, tipo in COLUMNAS[ruta][0]:
            if tipo in "ri":
                df[col] = pd.to_numeric(df[col].replace("", np.nan))
        return df.set_index(indice, drop=False) if indice else df

    def geojson(self) -> dict:
        return json.loads(self.p.archivo("datos/distritos.geojson").read_text(encoding="utf-8"))

    def aptos(self) -> set[str]:
        t = self.txt("salidas/tamizaje.csv")
        return set(t.loc[t["estado_tamizaje"] == "apto_tamizaje", "sitio_id"])


# ----------------------------------------------------------------------------- manifiesto y versiones
_CLAVES_MANIFIESTO = {
    "": ("paquete", "fecha", "descripcion", "dataset", "modelo", "mcda", "archivos"),
    "dataset": ("version", "archivo", "sha256", "filas", "positivos", "fondo"),
    "modelo": ("version", "algoritmo", "metricas_resumen"),
    "mcda": ("version", "top_k", "top_k_fraccion", "pesos", "tamizaje"),
}


def _v0(c: Lector) -> list[str]:
    m = c.p.manifest
    e = []
    for seccion, claves in _CLAVES_MANIFIESTO.items():
        d = m.get(seccion) if seccion else m
        if not isinstance(d, dict):
            e.append(f"manifest.{seccion}: falta o no es un objeto")
            continue
        e += [f"manifest{'.' + seccion if seccion else ''}.{k}: falta" for k in claves if k not in d]
    if e:
        return e
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(m["paquete"])):
        e.append(f"paquete {m['paquete']!r}: no es una versión semántica")
    elif int(m["paquete"].split(".")[0]) != PAQUETE_MAYOR_SOPORTADA:
        e.append(f"paquete {m['paquete']}: este backend soporta la versión mayor {PAQUETE_MAYOR_SOPORTADA}")
    if not str(m["modelo"]["version"]).startswith(PREFIJO_MODELO):
        e.append(f"modelo {m['modelo']['version']!r}: se espera un modelo de propensión ({PREFIJO_MODELO}…)")
    if not str(m["modelo"]["algoritmo"]).startswith(ALGORITMO_OFICIAL):
        e.append(f"modelo.algoritmo: el modelo oficial es {ALGORITMO_OFICIAL}; LightGBM u otros solo son comparación")
    archivos = {a.get("ruta"): a for a in m["archivos"]}
    ds = archivos.get(m["dataset"]["archivo"])
    if ds is None or ds.get("sha256") != m["dataset"]["sha256"]:
        e.append("manifest.dataset.sha256 no coincide con la entrada del dataset en manifest.archivos")
    try:
        card = c.p.json("modelo/model_card.json")
        param = c.p.json("modelo/logistica_parametros.json")
    except Exception as ex:
        return e + [f"modelo/: no se pudo leer model_card.json o logistica_parametros.json ({type(ex).__name__})"]
    if card.get("modelo") != m["modelo"]["version"]:
        e.append("model_card.modelo ≠ manifest.modelo.version")
    if card.get("algoritmo") != m["modelo"]["algoritmo"]:
        e.append("model_card.algoritmo ≠ manifest.modelo.algoritmo")
    if card.get("dataset_sha256") != m["dataset"]["sha256"]:
        e.append("model_card.dataset_sha256 ≠ manifest.dataset.sha256")
    if "contribuciones" not in param or "logit_base" not in param.get("contribuciones", {}):
        e.append("logistica_parametros.json: falta contribuciones.logit_base")
    return e


# ----------------------------------------------------------------------------- integridad
def _v1(c: Lector) -> list[str]:
    return c.p.errores_hash()


def _v2(c: Lector) -> list[str]:
    v = c.p.manifest["mcda"]["version"]
    e = []
    for r in ("salidas/mcda.csv", "salidas/mcda_montecarlo.csv"):
        encontradas = set(c.txt(r)["mcda_version"])
        if encontradas != {v}:
            e.append(f"{r}: mcda_version {sorted(encontradas)} ≠ manifiesto {v}")
    return e


def _v3(c: Lector) -> list[str]:
    e = []
    for f in c.txt("datos/fuentes.csv").to_dict("records"):
        estado, cod = f["licencia_estado"], f["codigo"]
        if estado == "no_verificada":
            e.append(f"{cod}: licencia no_verificada (un paquete así no se publica)")
        elif estado == "verificada" and not (f["licencia"] and f["licencia_url"] and f["licencia_verificada_en"]):
            e.append(f"{cod}: «verificada» exige licencia, licencia_url y licencia_verificada_en")
        elif estado == "sin_licencia_explicita" and not (
                f["licencia"] and f["atribucion"] and f["licencia_verificada_en"]):
            e.append(f"{cod}: «sin_licencia_explicita» exige licencia (base legal), atribucion y "
                     "licencia_verificada_en")
        if estado != "sin_licencia_explicita" and not f["licencia_url"]:
            e.append(f"{cod}: licencia_url vacía solo se admite en sin_licencia_explicita")
    return e


# ----------------------------------------------------------------------------- esquema
def _tipo_invalido(valores: pd.Series, tipo: str) -> int:
    if tipo == "r":
        return int((~np.isfinite(pd.to_numeric(valores, errors="coerce"))).sum())
    if tipo == "i":
        return int((~valores.str.fullmatch(r"-?\d+")).sum())
    if tipo == "b":
        return int((~valores.isin(["True", "False"])).sum())
    if tipo == "d":
        return int((~valores.str.fullmatch(r"\d{4}-\d{2}-\d{2}")).sum())
    return 0


def _v4(c: Lector) -> list[str]:
    e = []
    for ruta, (cols, admiten_nulos) in COLUMNAS.items():
        df = c.txt(ruta)
        esperadas = [n for n, _ in cols]
        if list(df.columns) != esperadas:
            e.append(f"{ruta}: columnas {list(df.columns)} ≠ {esperadas}")
            continue
        for n, tipo in cols:
            vacios = df[n] == ""
            if vacios.any() and n not in admiten_nulos:
                e.append(f"{ruta}.{n}: {int(vacios.sum())} nulos no admitidos")
            malos = _tipo_invalido(df.loc[~vacios, n], tipo)
            if malos:
                e.append(f"{ruta}.{n}: {malos} valores que no son del tipo «{tipo}»")
    if not e:
        t = c.txt("salidas/tamizaje.csv")
        pe = t["estado_tamizaje"] == "punto_existente"
        for n in NULOS_SOLO_PUNTO_EXISTENTE:
            k = int(((t[n] == "") & ~pe).sum())
            if k:
                e.append(f"tamizaje.{n}: {k} nulos fuera de punto_existente")
        excl = t["estado_tamizaje"] == "excluido_tamizaje"
        for n in ("codigo_exclusion", "motivo_exclusion"):
            if ((t[n] == "") & excl).any():
                e.append(f"tamizaje.{n}: nulo en un sitio excluido_tamizaje")
            if ((t[n] != "") & ~excl).any():
                e.append(f"tamizaje.{n}: lleno en un sitio que no es excluido_tamizaje")
    g = c.geojson()
    feats = g.get("features", [])
    if g.get("type") != "FeatureCollection" or len(feats) != 43:
        e.append("distritos.geojson: se esperaba una FeatureCollection de 43 distritos")
    else:
        for f in feats:
            if (f.get("geometry", {}).get("type") != "MultiPolygon"
                    or set(f.get("properties", {})) != {"ubigeo", "nombre"}):
                e.append(f"distritos.geojson: {f.get('properties', {}).get('ubigeo')} no es MultiPolygon con ubigeo "
                         "y nombre")
        ub = [f["properties"].get("ubigeo") for f in feats]
        if len(set(ub)) != 43 or not all(isinstance(u, str) and re.fullmatch(r"\d{6}", u) for u in ub):
            e.append("distritos.geojson: ubigeo repetido o no es texto de 6 dígitos")
    return e


def _v5(c: Lector) -> list[str]:
    e = []
    for ruta, enums in ENUMERACIONES.items():
        df = c.txt(ruta)
        for n, validos in enums.items():
            fuera = set(df.loc[df[n] != "", n]) - validos
            if fuera:
                e.append(f"{ruta}.{n}: valores fuera de la enumeración {sorted(fuera)}")
    patron = re.compile(r"\bviable\b", re.IGNORECASE)
    for ruta in COLUMNAS:
        df = c.txt(ruta)
        if any(patron.search(x) for x in df.columns) or df.apply(lambda s: s.str.contains(patron)).to_numpy().any():
            e.append(f"{ruta}: contiene «viable»")
    return e


# ----------------------------------------------------------------------------- coherencia entre archivos
_POR_SITIO = ("salidas/sitios.csv", "salidas/features.csv", "salidas/tamizaje.csv", "salidas/propension.csv",
              "salidas/propension_oof_por_semilla.csv")


def _v6(c: Lector) -> list[str]:
    e = []
    base = set(c.txt("salidas/sitios.csv")["sitio_id"])
    for r in _POR_SITIO:
        ids = c.txt(r)["sitio_id"]
        if ids.duplicated().any():
            e.append(f"{r}: sitio_id repetido")
        if set(ids) != base:
            e.append(f"{r}: conjunto de sitio_id distinto al de sitios.csv")
    ds = c.p.manifest["dataset"]
    if len(base) != ds["filas"]:
        e.append(f"sitios.csv: {len(base)} sitios ≠ {ds['filas']} del manifiesto")
    roles = c.txt("salidas/sitios.csv")["rol"].value_counts()
    if roles.get("positivo", 0) != ds["positivos"] or roles.get("fondo", 0) != ds["fondo"]:
        e.append(f"sitios.csv: roles {dict(roles)} ≠ manifiesto (positivos {ds['positivos']}, fondo {ds['fondo']})")
    malos = sorted(x for x in base if not PATRON_SITIO.match(x))
    if malos:
        e.append(f"sitio_id con patrón desconocido: {malos[:3]}")
    return e


def _v7(c: Lector) -> list[str]:
    s, p, t = (c.txt(r).set_index("sitio_id") for r in ("salidas/sitios.csv", "salidas/propension.csv",
                                                       "salidas/tamizaje.csv"))
    e = []
    if not (s["rol"] == p["rol"].reindex(s.index)).all():
        e.append("rol distinto entre sitios.csv y propension.csv")
    if not ((t["estado_tamizaje"].reindex(s.index) == "punto_existente") == (s["rol"] == "positivo")).all():
        e.append("estado_tamizaje = punto_existente no equivale a rol = positivo")
    return e


def _v8(c: Lector) -> list[str]:
    e = []
    aptos = c.aptos()
    esperado = c.p.manifest["mcda"]["tamizaje"]["apto_tamizaje"]
    if len(aptos) != esperado:
        e.append(f"tamizaje.csv: {len(aptos)} apto_tamizaje ≠ {esperado} del manifiesto")
    for r in ("salidas/criterios_mcda.csv", "salidas/mcda_montecarlo.csv"):
        ids = c.txt(r)["sitio_id"]
        if ids.duplicated().any() or set(ids) != aptos:
            e.append(f"{r}: sitios distintos a los {len(aptos)} apto_tamizaje")
    m = c.txt("salidas/mcda.csv")
    for esq, g in m.groupby("esquema"):
        if g["sitio_id"].duplicated().any() or set(g["sitio_id"]) != aptos:
            e.append(f"mcda.csv[{esq}]: sitios distintos a los apto_tamizaje")
    if len(m) != len(ESQUEMAS) * len(aptos) or set(m["esquema"]) != set(ESQUEMAS):
        e.append(f"mcda.csv: {len(m)} filas ≠ 3 × {len(aptos)}")
    return e


def _v9(c: Lector) -> list[str]:
    e = []
    ds = c.p.dataset().set_index("sitio_id")
    f = c.num("salidas/features.csv")
    s = c.num("salidas/sitios.csv")
    if set(ds.index) != set(f.index) or set(ds.index) != set(s.index):
        return ["features.csv o sitios.csv: sitios distintos a los del dataset congelado"]
    d = ds.loc[f.index]
    for v in VARIABLES:
        if not np.isclose(f[v].to_numpy(float), d[v].to_numpy(float), rtol=TOL_FEATURES, atol=0).all():
            e.append(f"features.csv.{v}: difiere del dataset congelado")
    if not (f["clase_oportunidad"] == d["clase_oportunidad"]).all():
        e.append("features.csv.clase_oportunidad: difiere del dataset congelado")
    d = ds.loc[s.index]
    for n in ("lon", "lat"):
        if not np.isclose(s[n].to_numpy(float), d[n].to_numpy(float), rtol=TOL_FEATURES, atol=0).all():
            e.append(f"sitios.csv.{n}: difiere del dataset congelado")
    iguales = ((s["ubigeo"].astype(int) == d["ubigeo"].astype(int)) & (s["distrito"] == d["distrito"])
               & (s["rol"] == d["rol"]) & (s["clase_oportunidad"] == d["clase_oportunidad"]))
    if not iguales.all():
        e.append(f"sitios.csv: {int((~iguales).sum())} sitios con ubigeo, distrito, rol o clase distintos al dataset")
    return e


# ----------------------------------------------------------------------------- coherencia numérica
def _v10(c: Lector) -> list[str]:
    p = c.num("salidas/propension.csv")
    o = c.num("salidas/propension_oof_por_semilla.csv").loc[p.index, [f"semilla_{i}" for i in range(10)]]
    e = []
    if (o.mean(axis=1) - p["propension_oof"]).abs().max() > TOL_SEMILLAS:
        e.append("propension_oof ≠ media de las 10 semillas")
    if (o.std(axis=1, ddof=1) - p["propension_oof_de"]).abs().max() > TOL_SEMILLAS:
        e.append("propension_oof_de ≠ desviación estándar muestral de las semillas")
    return e


def _v11(c: Lector) -> list[str]:
    p = c.num("salidas/propension.csv")
    e = []
    for valor, percentil in (("propension_oof", "percentil_oof"), ("propension_final", "percentil_final")):
        if (p[valor].rank(method="average") / len(p) - p[percentil]).abs().max() > TOL_PERCENTIL:
            e.append(f"{percentil} ≠ rango promedio de {valor} / n")
    return e


def _v12(c: Lector) -> list[str]:
    p = c.num("salidas/propension.csv")
    base = c.p.json("modelo/logistica_parametros.json")["contribuciones"]["logit_base"]
    pf = p["propension_final"]
    if not pf.between(0, 1, inclusive="neither").all():
        return ["propension_final fuera de (0, 1)"]
    dif = (np.log(pf / (1 - pf)) - base - p[[f"contrib_{g}" for g in GRUPOS_CONTRIB]].sum(axis=1)).abs().max()
    return [f"|logit(propension_final) − logit_base − Σ contrib| = {dif:.3g} ≥ {TOL_LOGIT}"] if dif >= TOL_LOGIT else []


def _v13(c: Lector) -> list[str]:
    e = []
    w = c.num("salidas/pesos_mcda.csv", indice="esquema")
    cr = c.num("salidas/criterios_mcda.csv")
    m = c.num("salidas/mcda.csv", indice=None)
    en_top = c.txt("salidas/mcda.csv")["en_top_k"] == "True"
    n = len(cr)
    mc = c.p.manifest["mcda"]
    k = mc["top_k"]
    if k != int(np.ceil(mc["top_k_fraccion"] * n)):
        e.append(f"top_k {k} ≠ ceil({mc['top_k_fraccion']} × {n})")
    if (w[CRITERIOS].sum(axis=1) - 1).abs().max() > TOL_MCDA:
        e.append("los pesos de algún esquema no suman 1")
    for esq in ESQUEMAS:
        if esq in mc["pesos"] and any(abs(mc["pesos"][esq][x] - w.loc[esq, x]) > 1e-6 for x in CRITERIOS):
            e.append(f"pesos_mcda[{esq}] ≠ manifest.mcda.pesos")
    for esq, g in m.groupby("esquema"):
        normas = cr.loc[g["sitio_id"], [f"{x}_norm" for x in CRITERIOS]].to_numpy()
        calc = normas @ w.loc[esq, CRITERIOS].to_numpy(float)
        if np.abs(calc - g["puntaje"].to_numpy()).max() > TOL_MCDA:
            e.append(f"mcda[{esq}]: puntaje ≠ Σ peso × K_norm")
        if sorted(g["rango"]) != list(range(1, n + 1)):
            e.append(f"mcda[{esq}]: rango no es una permutación de 1…{n}")
            continue
        if (np.diff(g.sort_values("rango")["puntaje"].to_numpy()) > 0).any():
            e.append(f"mcda[{esq}]: rango no ordena el puntaje de mayor a menor")
        if not ((g["rango"] <= k) == en_top.loc[g.index]).all():
            e.append(f"mcda[{esq}]: en_top_k ≠ (rango ≤ {k})")
    return e


def _v14(c: Lector) -> list[str]:
    e = []
    cr = c.num("salidas/criterios_mcda.csv")
    for x in CRITERIOS:
        if not cr[f"{x}_norm"].between(0, 1).all():
            e.append(f"{x}_norm fuera de [0, 1]")
    p = c.num("salidas/propension.csv")["propension_oof"].reindex(cr.index)
    if not np.isclose(cr["contexto_propension_oof"], p, rtol=1e-12, atol=0).all():
        e.append("contexto_propension_oof ≠ propension_oof")
    return e


def _v15(c: Lector) -> list[str]:
    e = []
    s = c.num("salidas/sitios.csv")
    (lo0, lo1), (la0, la1) = RECUADRO["lon"], RECUADRO["lat"]
    fuera = int((~(s["lon"].between(lo0, lo1) & s["lat"].between(la0, la1))).sum())
    if fuera:
        e.append(f"{fuera} sitios fuera del recuadro lon {RECUADRO['lon']}, lat {RECUADRO['lat']}")
    nombres = {int(f["properties"]["ubigeo"]): f["properties"]["nombre"] for f in c.geojson()["features"]}
    if set(nombres) != UBIGEOS:
        e.append("distritos.geojson no trae exactamente los ubigeo 150101–150143")
    desconocidos = set(s["ubigeo"].astype(int)) - UBIGEOS
    if desconocidos:
        e.append(f"ubigeo fuera de los 43 distritos: {sorted(desconocidos)}")
    distinto = sum(nombres.get(int(u)) != d for u, d in zip(s["ubigeo"], s["distrito"]))
    if distinto:
        e.append(f"{distinto} sitios con distrito distinto al nombre de su ubigeo en distritos.geojson")
    return e


_VALIDACIONES = {0: _v0, 1: _v1, 2: _v2, 3: _v3, 4: _v4, 5: _v5, 6: _v6, 7: _v7, 8: _v8, 9: _v9, 10: _v10,
                 11: _v11, 12: _v12, 13: _v13, 14: _v14, 15: _v15}


@dataclass
class Informe:
    paquete: Paquete
    lector: Lector
    resultados: list[Resultado]

    @property
    def fallos(self) -> list[Resultado]:
        return [r for r in self.resultados if not r.ok]

    @property
    def ok(self) -> bool:
        return not self.fallos


def validar(ruta: Path | str) -> Informe:
    """Ejecuta la validación 0 y las 1–15. Un error de lectura cuenta como fallo de esa validación.

    Si el manifiesto está incompleto (falla la 0), las demás no se evalúan: dependen de él.
    """
    paquete = Paquete(ruta)
    lector = Lector(paquete)
    resultados = []
    for n, fn in _VALIDACIONES.items():
        try:
            errores = fn(lector)
        except Exception as ex:  # archivo o columna ausente: el paquete se rechaza
            errores = [f"no se pudo evaluar: {type(ex).__name__}: {ex}"]
        resultados.append(Resultado(n, REGLAS[n], errores))
        if n == 0 and errores:
            break
    return Informe(paquete, lector, resultados)


def validar_o_rechazar(ruta: Path | str) -> Informe:
    """Como `validar`, pero lanza ErrorPaquete con el detalle si alguna validación falla."""
    informe = validar(ruta)
    if not informe.ok:
        resumen = "; ".join(f"[{r.n}] {r.errores[0]}" + (f" (+{len(r.errores) - 1})" if len(r.errores) > 1 else "")
                            for r in informe.fallos)
        raise ErrorPaquete(f"paquete rechazado: {resumen}", informe.fallos)
    return informe
