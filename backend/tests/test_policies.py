from datetime import UTC, datetime, timedelta

import pytest

from kleos.api.policies import PolicyService
from kleos.contracts.models import Actor, DomainError
from kleos.infrastructure.settings import Settings
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective, Project, ProjectStore


@pytest.fixture
def policy(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path}/policy.db")
    store.initialize_for_tests()

    def config(key, vision):
        return dict(
            key=key,
            provider="meta",
            model=key,
            version="1",
            protocol="openai_chat",
            baseUrl="https://models.example/v1",
            vision=vision,
            qualified=True,
            maxInputTokens=32000,
            inputPriceMicroUsdPerMillion=1000000,
            outputPriceMicroUsdPerMillion=2000000,
        )

    settings = Settings(
        model_catalog=[config("text", False), config("vision", True)],
        meta_api_key="fixture-secret",
        text_model_key="text",
        vision_model_key="vision",
    )
    actor = Actor(tenant_id="tenant", user_id="owner")
    project_id = store.create(actor, "Imported hardware", "create")["projectId"]
    return PolicyService(store, settings), actor, project_id


async def test_provider_change_does_not_cancel_running_objective_or_reset_its_snapshot(policy):
    service, actor, project_id = policy
    store = service.projects
    result = store.submit(actor, project_id, "Replace bracket", 1, "run")
    scope = RunScope(
        actor=actor,
        project_id=project_id,
        run_id=result["runId"],
        revision=2,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    choice = {"coordination": {"provider": "meta", "model": "vision"}}
    changed = service.update(actor, project_id, choice, 2, "policy")
    assert changed["revision"] == 3
    await GraphRepository(store).assert_active(scope)
    assert store.context(actor, project_id).revision == 2
    with store.transaction() as session:
        assert session.get(Objective, scope.run_id).data["modelPolicySnapshot"] == {}
        project = session.get(Project, project_id)
        assert project.data["policyRevision"] == 2 and project.data["modelPolicy"] == choice
    assert service.update(actor, project_id, choice, 2, "policy") == changed
    following = store.submit(actor, project_id, "New objective", 3, "next")
    with store.transaction() as session:
        assert session.get(Objective, following["runId"]).data["modelPolicySnapshot"] == choice
        assert session.get(Project, project_id).context_revision == 4
    with pytest.raises(DomainError, match="changed"):
        await GraphRepository(store).assert_active(scope)


def test_policy_cannot_select_text_only_capture_or_unconfigured_models(policy):
    service, actor, project = policy
    with pytest.raises(DomainError, match="image-capable"):
        service.update(actor, project, {"coordination": {"provider": "meta", "model": "text"}}, 1, "bad")
    with pytest.raises(DomainError, match="unavailable"):
        service.validate({"mechanics": {"provider": "meta", "model": "invented"}})
    with pytest.raises(DomainError):
        service.update(Actor(tenant_id="other", user_id="owner"), project, {}, 1, "unauthorized")


def test_later_content_changes_advance_context_after_policy_only_update(policy):
    service, actor, project_id = policy
    service.update(actor, project_id, {}, 1, "policy")
    with service.projects.transaction() as session:
        project = session.get(Project, project_id)
        assert project.revision == 2 and project.context_revision == 1
        project.advance_revision()
        assert project.revision == 3 and project.context_revision == 3
