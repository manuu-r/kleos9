"""The public resume lifecycle also supports one reused Runtime-created engine."""
import pytest
from test_prerequisite_runtime import (
    test_firmware_objective_approves_and_resumes_same_budget_and_thread as _full_prerequisite_journey,
)

from kleos.infrastructure.runtime import Runtime


@pytest.mark.parametrize('image', [False, True])
async def test_reused_runtime_engine_rebinds_planner_after_approved_revision(tmp_path, monkeypatch, image):
    original_factory = Runtime.engine_factory
    cached, scopes = {}, []

    def reused_factory(runtime, scope):
        scopes.append(scope)
        if id(runtime) not in cached:
            cached[id(runtime)] = original_factory(runtime, scope)
        return cached[id(runtime)]

    monkeypatch.setattr(Runtime, 'engine_factory', reused_factory)
    # Reuse the complete HTTP/provider/native-protocol fixture so this differs
    # from ordinary recovery only in engine object lifetime. Its assertions cover
    # explicit approval, original model/tool pins and budget identity, one native
    # dispatch, successful final planning, vision selection, and command replay.
    # Provider IO is HTTPX mock; native commands are an inert fixed-byte writer.
    await _full_prerequisite_journey(tmp_path, monkeypatch, image)
    assert len(cached) == 1 and len(scopes) == 2
    assert scopes[0].run_id == scopes[1].run_id and scopes[0].revision < scopes[1].revision
    assert scopes[0].model_copy(update={'revision': scopes[1].revision}) == scopes[1]
