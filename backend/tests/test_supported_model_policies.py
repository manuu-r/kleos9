import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_control_plane import create

from kleos.config import Settings
from kleos.contracts import ModelChoice
from kleos.db import Event, Project, now


def migration():
    path = Path(__file__).parents[1] / "migrations/versions/b40f239e7c12_supported_model_policies.py"
    spec = importlib.util.spec_from_file_location("supported_model_policies", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_supported_providers_can_be_selected():
    with pytest.raises(ValidationError):
        ModelChoice(provider="retired-provider", model="retired-model")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, default_model_provider="retired-provider")


def test_migration_updates_only_unsupported_choices_and_emits_one_event(platform):
    ident = create(platform)["projectId"]
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        original = dict(project.model_policy)
        project.model_policy = {**original, "firmware": {"provider": "retired-provider", "model": "old-model"}}
        revision, policy_revision, sequence = project.revision, project.policy_revision, project.event_sequence
    with platform.db.engine.begin() as connection:
        migration().normalize_policies(connection, "test-gemini")
        migration().normalize_policies(connection, "test-gemini")
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        assert project.model_policy == {**original, "firmware": {"provider": "google", "model": "test-gemini"}}
        assert project.revision == revision + 1 and project.policy_revision == policy_revision + 1
        assert project.event_sequence == sequence + 1
        events = session.scalars(select(Event).where(Event.project_id == ident,
                                                     Event.type == "modelPolicy.changed")).all()
        assert len(events) == 1 and events[0].data["changedLanes"] == ["firmware"]


def test_migration_keeps_valid_policy_and_deleted_tombstones_unchanged(platform):
    ident = create(platform)["projectId"]
    deleted_ident = create(platform, brief="Deleted policy fixture")["projectId"]
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        revision, policy = project.revision, project.model_policy
        deleted = session.get(Project, deleted_ident)
        deleted.model_policy = {"firmware": {"provider": "retired-provider", "model": "old-model"}}
        deleted.deleted_at = now()
        deleted_revision, deleted_policy = deleted.revision, deleted.model_policy
    with platform.db.engine.begin() as connection:
        migration().normalize_policies(connection, "test-gemini")
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        assert project.model_policy == policy and project.revision == revision
        deleted = session.get(Project, deleted_ident)
        assert deleted.model_policy == deleted_policy and deleted.revision == deleted_revision


def muse_migration():
    path = Path(__file__).parents[1] / "migrations/versions/d8e2f4a61c37_muse_model_policies.py"
    spec = importlib.util.spec_from_file_location("muse_model_policies", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_muse_migration_moves_only_unsupported_providers_and_keeps_gemini_choices(platform):
    ident = create(platform)["projectId"]
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        original = dict(project.model_policy)
        project.model_policy = {**original, "firmware": {"provider": "retired-provider", "model": "old"},
                                "review": {"provider": "retired-provider", "model": "old"},
                                "research": {"provider": "google", "model": "gemini-3.8-flash"}}
        policy_revision, sequence = project.policy_revision, project.event_sequence
    with platform.db.engine.begin() as connection:
        muse_migration().muse_policies(connection, "spark-test", "wonder-test")
        muse_migration().muse_policies(connection, "spark-test", "wonder-test")
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        assert project.model_policy == {**original, "firmware": {"provider": "meta", "model": "wonder-test"},
                                        "review": {"provider": "meta", "model": "spark-test"},
                                        "research": {"provider": "google", "model": "gemini-3.8-flash"}}
        assert project.policy_revision == policy_revision + 1 and project.event_sequence == sequence + 1
