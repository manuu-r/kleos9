"""Explicit Muse installations for the existing Meta/Modal credential settings.

Protocol/model capabilities are checked against documented provider contracts and
mocked wire fixtures. This is not a claim that a particular account has live access.
"""
from pydantic_settings import DotEnvSettingsSource

# The only settings read from the old backend's `.env`. That file shares the KLEOS_ prefix, so everything else in
# it (database, authentication, native execution, physical policy) must never reach the fresh runtime.
PROVIDER_NAMES = {
    'KLEOS_META_API_KEY': 'KLEOS_META_API_KEY',
    'KLEOS_MODAL_LLM_KEY': 'KLEOS_MODAL_API_KEY',
    'KLEOS_MODAL_LLM_URL': 'KLEOS_MODAL_BASE_URL',
}


class ProviderFileSource(DotEnvSettingsSource):
    def __call__(self):
        if self.current_state.get('environment') == 'test':
            return {}
        result = {}
        for legacy, current in PROVIDER_NAMES.items():
            # New names win within a file; process environment wins over this source.
            for name in (current, legacy):
                value = self.env_vars.get(name.lower())
                if value is not None:
                    result[current] = value
                    break
        return result


def muse_catalog(*, meta_enabled, modal_enabled, modal_url):
    from kleos.orchestration.models import ModelConfiguration
    common = dict(protocol='openai_chat', version='documented-wire-2026-11-03',
        vision=True, tool_calls=True, qualified=True, max_input_tokens=131072, max_output_tokens=16384,
        input_price_micro_usd_per_million=1250000, output_price_micro_usd_per_million=4250000)
    models = []
    if meta_enabled:
        models.append(ModelConfiguration(key='meta-muse-spark', provider='meta', model='muse-spark-1.3',
            base_url='https://api.meta.ai/v1', **common))
    if modal_enabled:
        base = modal_url.rstrip('/')
        if not base.endswith('/v1'):
            base += '/v1'
        models.append(ModelConfiguration(key='modal-muse-spark-open', provider='modal',
            model='meta-models/Muse-Spark-Open-816B-A42B', base_url=base, **common))
    return [model.model_dump(mode='json') for model in models]
