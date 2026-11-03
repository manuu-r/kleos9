"""Frontend catalogs reflect installed configuration, never invented provider defaults."""

from kleos.orchestration.models import ModelConfiguration

LANES = ("coordination", "research", "mechanics", "electronics", "firmware", "application", "review")
NATIVE_LANES = {
    "cad": "mechanics",
    "pcb": "electronics",
    "firmware": "firmware",
    "application": "application",
}


class CatalogFacade:
    def __init__(self, settings, registry, *, native_pins=()):
        self.settings, self.registry, self.native_pins = settings, registry, list(native_pins)

    def models(self):
        configurations = [ModelConfiguration.model_validate(item) for item in self.settings.model_catalog]
        by_key = {item.key: item for item in configurations}
        credentials = {"meta": self.settings.meta_api_key, "modal": self.settings.modal_api_key}

        def available(item):
            credential = credentials[item.provider]
            return bool(item.qualified and item.tool_calls and credential and credential.get_secret_value())

        def lanes(item):
            if not available(item):
                return []
            # The capture selector must not default to a text-only model.
            return list(LANES if item.vision else LANES[1:])

        defaults = {}
        for lane in LANES:
            key = self.settings.vision_model_key if lane == "coordination" else self.settings.text_model_key
            chosen = by_key.get(key)
            if chosen and lane in lanes(chosen):
                defaults[lane] = {"provider": chosen.provider, "model": chosen.model}
        return {
            "models": [
                {
                    "provider": item.provider,
                    "model": item.model,
                    "configured": available(item),
                    "qualification": "qualified" if item.qualified else "unqualified",
                    "enabledLanes": lanes(item),
                    "capabilities": [
                        "text",
                        *(["tools"] if item.tool_calls else []),
                        *(["vision"] if item.vision else []),
                    ],
                    "creditBalance": None,
                    "contextCharacterCeiling": min(self.settings.max_context_chars, item.max_input_tokens),
                    "pricingVersion": item.version,
                    "contextLimit": item.max_input_tokens,
                }
                for item in configurations
            ],
            "defaultPolicy": defaults,
            "billing": "Estimated usage is recorded per objective; provider account balances are not inferred.",
        }

    def engineering_tools(self):
        from kleos.api.tool_policy import ToolInstallations
        return ToolInstallations(self.registry, native_pins=self.native_pins).catalog()

    def query(self, operation):
        return self.models() if operation == "model_catalog" else self.engineering_tools()
