"""
tests/test_ml_zone_mapping.py

Tests del mapeo candidate_zones -> payload /predict de ecolima-ml
(app/services/ml_zone_mapping.py) y de su tipado (app/schemas/ml.py).

Todo mockeado / sin red: la instancia de ecolima-ml está caída (sin crédito
Azure). Los tests de integración contra la API viva quedan marcados como
skip -- ver el final del archivo.
"""
import pytest
from pydantic import ValidationError

from app.schemas.ml import MLZoneFeatures
from app.services.ml_zone_mapping import (
    UNMAPPED_ML_FEATURES,
    map_candidate_zone_to_ml_payload,
)

# Fila conocida de candidate_zones (Sección B + cell_area_km2 + zone_id).
# Valores elegidos a mano para poder verificar cada conversión de unidad.
KNOWN_ZONE_ROW = {
    "zone_id": 42,
    "population_density": 8500.5,
    "income_stratum": 3,
    "fuel_expenditure_sol": 45.0,
    "edu_level_head": 3.2,
    "household_size_avg": 3.5,
    "pct_nse_ab": 62.0,
    "pct_nse_de": 10.0,
    "gpc_kg_per_capita_day": 0.85,
    "pct_recyclable": 28.0,
    "pct_plastic": 22.0,
    "informal_recyclers_count": 5,
    "road_density": 12000.0,
    "dist_to_main_road_m": 300.0,
    "dist_to_market_m": 600.0,
    "dist_to_nearest_point_m": 1500.0,
    "existing_points_500m": 1,
    "has_park_300m": True,
    "slope_pct": 8.0,
    "urbanized_area_pct": 70.0,
    "recycling_potential_index": 200.0,
    "cell_area_km2": 0.0625,
}

EXPECTED_PAYLOAD = {
    "candidate_id": 42,
    "population_density": 8500.5,
    "dist_nearest_road_m": 300.0,
    "dist_nearest_recycling_m": 1500.0,
    "waste_per_capita_kg": 0.85,
    "nse_ab_pct": 0.62,
    "nse_de_pct": 0.10,
    "urbanization_rate": 0.70,
    "nse_c_pct": 0.28,
    "area_m2": 62500.0,
    "has_park_300m": True,
}


def test_mapping_produces_expected_payload():
    """Fila conocida -> payload exacto (mapeos directos + conversión 0-100 -> 0-1
    + area_m2 desde cell_area_km2 + nse_c_pct derivado)."""
    payload = map_candidate_zone_to_ml_payload(KNOWN_ZONE_ROW)
    assert payload == EXPECTED_PAYLOAD


def test_mapping_never_emits_server_recalculated_fields():
    """accessibility_composite, nse_high_ratio, recycling_deficit y
    coverage_gap_index nunca deben aparecer -- el servidor las recalcula
    siempre o no tenemos un input real equivalente (contrato §2)."""
    payload = map_candidate_zone_to_ml_payload(KNOWN_ZONE_ROW)
    forbidden = {
        "accessibility_composite", "nse_high_ratio",
        "recycling_deficit", "coverage_gap_index", "poi_parks_500m",
    }
    assert forbidden.isdisjoint(payload.keys())


def test_mapping_never_emits_unmapped_features():
    """Las features sin fuente real en candidate_zones no se inventan --
    simplemente no aparecen en el payload."""
    payload = map_candidate_zone_to_ml_payload(KNOWN_ZONE_ROW)
    assert UNMAPPED_ML_FEATURES.isdisjoint(payload.keys())


def test_mapping_omits_missing_source_columns():
    """Si la fila no trae una columna origen, el payload simplemente la
    omite -- nunca manda None/null para rellenar."""
    partial_row = {"zone_id": 1, "population_density": 5000.0}
    payload = map_candidate_zone_to_ml_payload(partial_row)
    assert payload == {"candidate_id": 1, "population_density": 5000.0}
    assert None not in payload.values()


def test_mapping_output_validates_against_schema():
    """El payload que produce el mapeo debe ser un MLZoneFeatures válido."""
    payload = map_candidate_zone_to_ml_payload(KNOWN_ZONE_ROW)
    zone = MLZoneFeatures(**payload)
    assert zone.population_density == 8500.5
    assert zone.nse_ab_pct == 0.62
    assert zone.has_park_300m is True


# ── Payload real de smoke_api_contract.py (contrato §4) ───────────────────────
# Fila 0 de data/synthetic/dataset_entrenamiento_simulado_v0_3.csv en
# ecolima-ml, tal como la reporta contrato-ml-api.md. Ata nuestro tipo al
# contrato real: si ecolima-ml cambia esta forma, este test debe fallar.
SMOKE_CONTRACT_EXAMPLE_PAYLOAD = {
    "candidate_id": "ECO-SIM-V03-0001",
    "population_density": 17777.0,
    "num_households": 1587,
    "nse_ab_pct": 0.6622,
    "nse_c_pct": 0.377,
    "nse_de_pct": 0.0,
    "urbanization_rate": 0.7717,
    "dist_nearest_road_m": 2.9,
    "walkability_score": 0.6904,
    "poi_commercial_500m": 42,
    "poi_educational_500m": 5,
    "poi_parks_500m": 0,
    "land_use_encoded": 0,
    "area_m2": 91.6,
    "dist_nearest_recycling_m": 160.3,
    "recycling_density_1km": 0,
    "waste_per_capita_kg": 0.922,
    "coverage_gap_index": 0.0595,
    "accessibility_composite": 0.8104,
    "nse_high_ratio": 0.6622,
    "recycling_deficit": 0,
}


def test_schema_accepts_smoke_contract_example_payload():
    """El esquema debe aceptar el payload real documentado en contrato-ml-api.md
    §4 (incluye las 3 derivadas que el servidor recalcula igual -- el schema
    las tipa para poder validar payloads reales como este, aunque nuestro
    propio mapeo nunca las escriba, ver test_mapping_never_emits_*)."""
    zone = MLZoneFeatures(**SMOKE_CONTRACT_EXAMPLE_PAYLOAD)
    assert zone.candidate_id == "ECO-SIM-V03-0001"
    assert zone.recycling_deficit == 0
    assert zone.land_use_encoded == 0


def test_schema_rejects_wrong_types():
    """Protección que antes no existía (dict[str, Any] no validaba nada):
    un valor con el tipo equivocado debe fallar en validación."""
    with pytest.raises(ValidationError):
        MLZoneFeatures(population_density="mucha")


def test_schema_rejects_out_of_range_ratio():
    """nse_ab_pct está documentado 0-1 (contrato §3) -- un valor en escala
    0-100 sin convertir (bug de unidades típico) debe fallar, no pasar
    silenciosamente como hace hoy dict[str, Any]."""
    with pytest.raises(ValidationError):
        MLZoneFeatures(nse_ab_pct=62.0)  # el bug real: olvidar dividir /100


def test_schema_rejects_invalid_land_use_encoded():
    """land_use_encoded solo admite 0-3 (contrato §3: residencial/comercial/parque/mixto)."""
    with pytest.raises(ValidationError):
        MLZoneFeatures(land_use_encoded=9)


# ── Integración contra la API viva -- NO ejecutable hoy ────────────────────────
# La instancia de ecolima-ml (puerto 8001, Azure) está caída por falta de
# crédito. Estos tests quedan documentados y skip hasta que esté arriba --
# no se activa nada de esto en esta ronda (ver ml_service.run_inference,
# punto de conmutación apagado).

@pytest.mark.skip(reason="API ML (ecolima-ml, puerto 8001) caída -- sin crédito Azure. Ver contrato-ml-api.md.")
@pytest.mark.asyncio
async def test_predict_live_api_accepts_mapped_payload():
    """Cuando la API esté arriba: mandar el payload de un candidate_zone real
    mapeado por map_candidate_zone_to_ml_payload y confirmar 200, no 400 --
    valida en la práctica el hallazgo del punto 2 (features faltantes)."""
    pass


@pytest.mark.skip(reason="API ML (ecolima-ml, puerto 8001) caída -- sin crédito Azure. Ver contrato-ml-api.md.")
@pytest.mark.asyncio
async def test_model_metadata_live_api_matches_documented_shape():
    """Cuando la API esté arriba: GET /model/metadata debe validar contra
    MLModelMetadataResponse y feature_names debe coincidir con lo documentado
    en contrato-ml-api.md §5 para lgbm_recycling_simulated_v0_3."""
    pass
