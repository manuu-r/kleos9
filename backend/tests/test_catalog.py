import json
from pathlib import Path

from kleos.api.catalog import CatalogFacade
from kleos.capabilities.registry import CapabilityRegistry
from kleos.infrastructure.settings import Settings


def model(key, *, vision=False):
    return {
        "key": key,
        "provider": "meta",
        "model": key,
        "protocol": "openai_chat",
        "version": "1",
        "baseUrl": "https://models.example/v1",
        "qualified": True,
        "vision": vision,
        "maxInputTokens": 32000,
        "inputPriceMicroUsdPerMillion": 1000000,
        "outputPriceMicroUsdPerMillion": 2000000,
    }


def test_missing_model_configuration_has_no_invented_default():
    result = CatalogFacade(Settings(_env_file=None, environment='test', meta_api_key=None,
        modal_api_key=None), CapabilityRegistry()).models()
    assert result["models"] == [] and result["defaultPolicy"] == {}


def test_coordination_default_only_uses_explicit_available_vision_model():
    settings = Settings(
        _env_file=None, environment='test',
        model_catalog=[model("text"), model("vision", vision=True)],
        text_model_key="text",
        vision_model_key="vision",
        meta_api_key="private-test-key",
    )
    result = CatalogFacade(settings, CapabilityRegistry()).models()
    assert result["defaultPolicy"]["coordination"]["model"] == "vision"
    assert result["defaultPolicy"]["mechanics"]["model"] == "text"
    assert "coordination" not in result["models"][0]["enabledLanes"]
    assert "private-test-key" not in json.dumps(result)
    schema = json.loads((Path(__file__).parents[1] / "contracts/openapi.json").read_text())["components"][
        "schemas"
    ]
    assert set(result) == set(schema["ModelCatalogView"]["properties"])
    for row in result["models"]:
        assert set(row) == set(schema["ModelView"]["properties"])


def test_uncredentialed_models_cannot_be_selected_by_default():
    settings = Settings(
        _env_file=None, environment='test', meta_api_key=None, modal_api_key=None,
        model_catalog=[model("vision", vision=True)], text_model_key="vision", vision_model_key="vision"
    )
    result = CatalogFacade(settings, CapabilityRegistry()).models()
    assert not result["models"][0]["configured"] and result["defaultPolicy"] == {}
    assert CatalogFacade(settings, CapabilityRegistry()).engineering_tools()["tools"] == []
