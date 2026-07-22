"""
tests/test_ml_model_contract.py
Test de contrato: FEATURE_COLUMNS (ml_service.py) debe coincidir en nombre
y orden con lo que espera el artefacto realmente cargado por load_model().

FEATURE_COLUMNS es una copia mantenida a mano de ALL_FEATURES en el repo
ecolima-ml — nada impide que se desincronicen en silencio. Un desajuste de
orden produce predicciones incorrectas sin que nada falle ruidosamente en
producción (LightGBM no valida nombres de columnas al recibir un array
numpy). Este test es la única red de seguridad contra ese escenario.
"""
import pytest

from app.services import ml_service

pytestmark = pytest.mark.asyncio


async def test_feature_columns_match_loaded_model_artifact():
    ml_service._model_cache.clear()
    model = ml_service.load_model("latest")

    names = ml_service._extract_feature_names(model)

    assert names is not None, (
        "El artefacto cargado no expone sus feature names "
        "(ni .feature_name(), ni .booster_.feature_name(), ni dict['feature_names']) "
        "— no se puede validar el contrato automáticamente."
    )
    assert names == ml_service.FEATURE_COLUMNS, (
        "Desincronización entre FEATURE_COLUMNS (backend) y el artefacto cargado.\n"
        f"Backend ({len(ml_service.FEATURE_COLUMNS)}):   {ml_service.FEATURE_COLUMNS}\n"
        f"Artefacto ({len(names)}): {names}"
    )
