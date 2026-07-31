"""
Mapeo puro: fila de candidate_zones (Sección B) -> payload `zone` que espera
POST /predict de ecolima-ml.

Fuente de verdad: `contrato-ml-api.md` (auditoría de solo lectura del repo
ecolima-ml — la instancia desplegada está caída, nada de esto se verificó
por ejecución contra la API real). Las referencias `contrato §N` apuntan a
las secciones de ese documento.

Standalone a propósito: NADA de este módulo se llama desde run_inference
todavía — ver el TODO en `ml_service.run_inference` (punto de conmutación,
apagado). Este módulo existe para que el mapeo se pueda diseñar, revisar y
testear antes de conectar nada.

── Hallazgo central: los dos sets de features casi no se superponen ──────────
FEATURE_COLUMNS (este backend, 20 features) y las 16 features crudas que
espera ecolima-ml (contrato §3) fueron diseñados por separado y comparten
muy poco. De las 16 crudas de ecolima-ml, candidate_zones aporta datos reales
para 8 (contando el puente has_park_300m) más 1 derivable con una asunción
explícita (nse_c_pct). Las otras 6 (ver UNMAPPED_ML_FEATURES) no tienen
ninguna columna equivalente en candidate_zones hoy — no se inventan valores
para ellas: el payload simplemente las omite. Eso significa que, tal como
está HOY el dataset del backend, un payload de este mapeo casi con toda
seguridad NO alcanza para que el ColumnTransformer de ecolima-ml prediga sin
error (contrato: "no se pudo verificar por ejecución... KeyError/ValueError
esperado por lectura de la librería"). Resolver esto es trabajo pendiente,
no algo que este mapeo pueda resolver inventando datos.
"""

from typing import Any

# Features crudas que ecolima-ml espera (contrato §3) y para las que
# candidate_zones NO tiene ninguna columna equivalente hoy. No se mandan
# -- ni con un valor inventado ni como null -- se omiten del payload.
# poi_parks_500m NO está en esta lista: se cubre indirectamente mandando
# has_park_300m (ver el puente de compatibilidad más abajo).
UNMAPPED_ML_FEATURES: frozenset[str] = frozenset({
    "num_households",
    "walkability_score",
    "poi_commercial_500m",
    "poi_educational_500m",
    "land_use_encoded",
    "recycling_density_1km",
})

# Features que el servidor de ecolima-ml SIEMPRE recalcula si sus raw están
# presentes (contrato §2, preprocessing.py:53-65) -- cualquier valor que se
# mande se ignora en silencio ("silent override"). Este mapeo nunca las
# escribe. (No es una lista que se use en el código -- documentada para que
# quede explícito por qué no aparecen abajo.)
_ALWAYS_RECALCULATED_SERVER_SIDE = (
    "accessibility_composite", "nse_high_ratio", "recycling_deficit",
)


def map_candidate_zone_to_ml_payload(zone: dict[str, Any]) -> dict[str, Any]:
    """
    Traduce una fila de candidate_zones (dict con, al menos, las columnas de
    Sección B + cell_area_km2 + zone_id) al dict `zone` de MLZoneFeatures.

    Reglas (contrato §2 — crudas vs. derivadas):
      - Mapeos directos: mismo concepto y unidad en ambos lados.
      - Mapeos con conversión: candidate_zones guarda varios porcentajes en
        escala 0-100 (confirmado en ml_service.generate_synthetic_training_data);
        ecolima-ml los espera en 0-1 (contrato §3) -- se divide entre 100.
      - has_park_300m se manda TAL CUAL, en vez de inventar poi_parks_500m:
        el servidor deriva poi_parks_500m = has_park_300m.astype(float) si
        poi_parks_500m no viene (contrato §2/§3, preprocessing.py:72-73) --
        es el único puente de compatibilidad documentado en el contrato.
      - accessibility_composite, nse_high_ratio, recycling_deficit: NUNCA se
        mandan. El servidor las recalcula siempre que sus raw estén
        presentes, así que mandarlas no tendría efecto (contrato §2) --
        sería mandar un valor que el servidor va a descartar en silencio.
      - coverage_gap_index: no se manda. candidate_zones no tiene un input
        de Sección B equivalente (coverage_gap_m es un OUTPUT de
        run_inference, en metros, semántica distinta). Se deja que el
        servidor la derive de recycling_deficit (contrato §2).
      - Todo lo que está en UNMAPPED_ML_FEATURES se omite -- no se manda
        `null` ni un placeholder. Ver el docstring del módulo.

    Campos ausentes o None en `zone` se omiten del payload (no se manda
    `None` para una columna que la fila no tiene) -- así el payload
    resultante siempre es válido contra MLZoneFeatures sin necesitar
    valores ficticios.
    """
    payload: dict[str, Any] = {}

    if zone.get("zone_id") is not None:
        payload["candidate_id"] = zone["zone_id"]

    # ── Mapeos directos (mismo concepto, misma unidad) ──────────────────────
    if zone.get("population_density") is not None:
        payload["population_density"] = float(zone["population_density"])
    if zone.get("dist_to_main_road_m") is not None:
        payload["dist_nearest_road_m"] = float(zone["dist_to_main_road_m"])
    if zone.get("dist_to_nearest_point_m") is not None:
        payload["dist_nearest_recycling_m"] = float(zone["dist_to_nearest_point_m"])
    if zone.get("gpc_kg_per_capita_day") is not None:
        # Mismo concepto (generación de residuos per cápita) pero el
        # periodo de waste_per_capita_kg no está confirmado en el contrato
        # (¿por día, como gpc_kg_per_capita_day, o total?) -- se mapea
        # directo, marcado como riesgo, no como hecho verificado.
        payload["waste_per_capita_kg"] = float(zone["gpc_kg_per_capita_day"])

    # ── Mapeos con conversión de unidad (candidate_zones: 0-100 -> ecolima-ml: 0-1) ──
    if zone.get("pct_nse_ab") is not None:
        payload["nse_ab_pct"] = float(zone["pct_nse_ab"]) / 100
    if zone.get("pct_nse_de") is not None:
        payload["nse_de_pct"] = float(zone["pct_nse_de"]) / 100
    if zone.get("urbanized_area_pct") is not None:
        payload["urbanization_rate"] = float(zone["urbanized_area_pct"]) / 100

    # nse_c_pct: candidate_zones no distingue NSE C. Se deriva ASUMIENDO que
    # AB + C + DE particionan el 100% de los hogares -- asunción de este
    # mapeo, NO confirmada contra la definición real de ecolima-ml. Se
    # recorta a [0, 1] por si los datos de origen son inconsistentes.
    if zone.get("pct_nse_ab") is not None and zone.get("pct_nse_de") is not None:
        nse_c = 1 - (float(zone["pct_nse_ab"]) + float(zone["pct_nse_de"])) / 100
        payload["nse_c_pct"] = max(0.0, min(1.0, nse_c))

    # area_m2: candidate_zones guarda el área de la celda en km2.
    if zone.get("cell_area_km2") is not None:
        payload["area_m2"] = float(zone["cell_area_km2"]) * 1_000_000

    # ── Puente de compatibilidad documentado (contrato §2, §3) ──────────────
    if zone.get("has_park_300m") is not None:
        payload["has_park_300m"] = bool(zone["has_park_300m"])

    return payload
