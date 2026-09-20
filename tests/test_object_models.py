import pytest

from visual_ai_agent.object_models import OBJECT_MODEL_PRESETS, get_object_model_preset

EXPECTED = {
    "original": "9c60d351bb2865a8169d0590c07c905b372e81e4a846a8ff920d244955e2516c",
    "r03": "f71abc2197aeb433fb6ba21f97113b5797c2bdbbaeded3fe37466e5ee1a4d004",
    "r04": "35843f82e24e1a2667be27f616ff45b4210f01f68a6fde01ffd108c77fab0533",
    "r05": "fbbaa834b563fc5672b652fc5742b5cd305c3a0b0751949777c8f5c43538f25d",
}


def test_object_model_registry_has_stable_ids_hashes_and_absolute_lookup_paths():
    assert {preset.id: preset.sha256 for preset in OBJECT_MODEL_PRESETS} == EXPECTED
    for model_id in EXPECTED:
        resolved = get_object_model_preset(model_id)
        assert resolved.path.is_absolute()
        assert resolved.path == resolved.path.resolve()


def test_unknown_object_model_is_rejected():
    with pytest.raises(ValueError, match="Unknown object model preset"):
        get_object_model_preset("unknown")
