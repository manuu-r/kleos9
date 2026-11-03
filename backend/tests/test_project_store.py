import pytest

from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import Membership, ProjectStore


@pytest.fixture
def store(tmp_path):
    value = ProjectStore(f"sqlite:///{tmp_path}/fresh.db")
    value.initialize_for_tests()
    return value


ACTOR = Actor(tenant_id="one", user_id="owner")


def test_scoped_objective_survives_restart_and_does_not_require_device_stages(store):
    p = store.create(ACTOR, "Existing robot with a missing bracket", "create")
    result = store.submit(ACTOR, p["projectId"], "Build only the missing bracket", 1, "submit")
    assert store.submit(ACTOR, p["projectId"], "Build only the missing bracket", 1, "submit") == result
    reopened = ProjectStore(str(store.engine.url))
    assert reopened.context(ACTOR, p["projectId"]).revision == 2
    assert [e["type"] for e in reopened.events(ACTOR, p["projectId"])] == [
        "project.created",
        "objective.submitted",
    ]
    with pytest.raises(DomainError, match="different input"):
        store.submit(ACTOR, p["projectId"], "Change everything", 1, "submit")
    with pytest.raises(DomainError, match="Project changed"):
        store.submit(ACTOR, p["projectId"], "Second objective", 1, "next")


@pytest.mark.parametrize(
    "actor", [Actor(tenant_id="two", user_id="owner"), Actor(tenant_id="one", user_id="other")]
)
def test_context_events_and_receipts_enforce_scope(store, actor):
    p = store.create(ACTOR, "Device", "create")
    with pytest.raises(DomainError):
        store.context(actor, p["projectId"])
    with pytest.raises(DomainError):
        store.events(actor, p["projectId"])


def test_revoked_membership_cannot_read_idempotent_receipt(store):
    p = store.create(ACTOR, "Device", "create")
    with store.transaction() as s:
        s.delete(s.get(Membership, (p["projectId"], ACTOR.user_id)))
    with pytest.raises(DomainError):
        store.create(ACTOR, "Device", "create")
