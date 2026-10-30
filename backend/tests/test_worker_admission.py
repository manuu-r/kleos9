import pytest

from kleos.contracts import DomainError
from kleos.worker import index_with_capacity


async def test_indexing_waits_for_shared_capacity_without_changing_source_identity():
    calls, waits = [], []

    async def index(source_id):
        calls.append(source_id)
        if len(calls) < 3:
            raise DomainError("Full", 429, "model_capacity")
        return "indexed"

    async def sleep(seconds):
        waits.append(seconds)

    assert await index_with_capacity(index, "source-1", sleep) == "indexed"
    assert calls == ["source-1"] * 3 and waits == [2, 2]


async def test_uncertain_embedding_outcome_is_never_automatically_retried():
    calls = []

    async def index(source_id):
        calls.append(source_id)
        raise DomainError("Uncertain", 409, "compute_uncertain")

    async def never_sleep(_seconds):
        pytest.fail("Uncertain calls must require reconciliation")

    with pytest.raises(DomainError, match="Uncertain"):
        await index_with_capacity(index, "source-1", never_sleep)
    assert calls == ["source-1"]
