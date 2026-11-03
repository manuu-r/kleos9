"""Disposable opt-in PostgreSQL: opposite approvers share one account lock order."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from test_access import joined, owner
from test_interfaces import Model
from test_orchestration import Planner
from test_postgres_runtime import database  # noqa: F401

from kleos.access.service import AccessService, AccessSettings
from kleos.contracts.models import TaskSpec
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision, RunScope
from kleos.orchestration.prerequisites import InterfacePrerequisites
from kleos.orchestration.worker import ObjectiveWorker
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.interfaces import InterfaceService
from kleos.projects.prerequisites import PrerequisiteService
from kleos.projects.store import Membership, Objective, Project


async def test_opposite_project_approvers_lock_accounts_in_same_order_and_replay(database, monkeypatch):  # noqa: F811
    projects, _ = database
    projects.require_account_authority = True
    access = AccessService(projects, AccessSettings(secret_key='s'*40, environment='test', setup_token='setup-secret'))
    first, _, _ = owner(access)
    second, _, _ = joined(access, first, role='member')
    actors = [first, second]
    repository = GraphRepository(projects)
    interfaces = InterfaceService(projects)
    service = PrerequisiteService(projects, interfaces, repository)
    saver, planners = InMemorySaver(), {}
    model = Model(output={'summary': 'Review the exact firmware board.', 'sourceIds': ['brief'],
        'interfaces': {'firmware': {'board': 'nrf52840dk/nrf52840'}},
        'limitations': ['Protocol fixture, no firmware behavior or native execution claim.']})
    bridge = InterfacePrerequisites(repository, service, lambda scope, context: model)
    class NoNative:
        async def execute(self, *args):
            pytest.fail('Approval concurrency must never start native generation')
    def factory(scope):
        # Trusted fixture metadata permits proposal testing without installing a tool image.
        with projects.transaction() as session:
            run = session.get(Objective, scope.run_id)
            run.data = {**run.data, 'runtimePins': {'native': [{'capability': 'firmware',
                'firmwareBoard': 'nrf52840dk/nrf52840', 'installationHash': 'a'*64}]}}
        return ObjectiveEngine(repository, planners[scope.run_id], NoNative(), checkpointer=saver, prerequisites=bridge)
    worker = ObjectiveWorker(projects, factory)
    entries = []
    for index, actor in enumerate(actors):
        project_id = projects.create(actor, 'Opposite-approver concurrency fixture', 'create-project')['projectId']
        # Explicit test project sharing; account and organization identities above
        # come from real setup/invitation/signup services.
        with projects.transaction() as session:
            session.add(Membership(project_id=project_id, user_id=actors[1-index].user_id, role='editor'))
        run_id = projects.submit(actor, project_id, 'Implement missing firmware', 1, 'objective')['runId']
        planners[run_id] = Planner([PlanDecision(tasks=[TaskSpec(id='firmware', capability='firmware',
            objective='Missing firmware only', allowed_paths=['firmware/src/main.c'])])])
        assert await worker.process(run_id)
        with projects.transaction() as session:
            run, project = session.get(Objective, run_id), session.get(Project, project_id)
            assert run.status == 'waiting' and run.data['interruptId']
            assert run.data['question']['kind'] == 'interface_prerequisite'
            entries.append({'actor': actors[1-index], 'creator': actor, 'projectId': project_id,
                'runId': run_id, 'scope': RunScope.model_validate(run.data['scope']),
                'body': {'type': 'project.message', 'expectedRevision': project.revision,
                         'payload': {'projectId': project_id, 'text': 'approve interfaces'}}})

    original = projects.authorize_account
    barrier, local, lock = threading.Barrier(2), threading.local(), threading.Lock()
    sequences = {}
    def observed(session, actor, *, write=False):
        if getattr(local, 'entry', None) is not None:
            local.calls += 1
            # dispatch_message first performs its read-routing authorization in
            # a separate transaction. The next account lock is the mutation's
            # first lock: synchronize both different Project rows at that point.
            if local.calls >= 2:
                with lock:
                    values = sequences.setdefault(local.entry, [])
                    if actor.user_id not in values:
                        values.append(actor.user_id)
            if local.calls == 2:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                session.execute(text("SET LOCAL statement_timeout = '8s'"))
                barrier.wait(timeout=8)
        return original(session, actor, write=write)
    monkeypatch.setattr(projects, 'authorize_account', observed)
    def approve(index):
        entry = entries[index]
        local.entry, local.calls = index, 0
        return service.dispatch_message(entry['actor'], entry['body'], 'approve-opposite')
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(approve, index) for index in range(2)]
        receipts = [future.result(timeout=15) for future in pending]
    expected = sorted(actor.user_id for actor in actors)
    assert sequences == {0: expected, 1: expected}
    assert model.calls == 2
    reopened = PrerequisiteService(projects, InterfaceService(projects), GraphRepository(projects))
    for entry, receipt in zip(entries, receipts, strict=True):
        assert reopened.dispatch_message(entry['actor'], entry['body'], 'approve-opposite') == receipt
        with projects.transaction() as session:
            project, run = session.get(Project, entry['projectId']), session.get(Objective, entry['runId'])
            assert project.data['acceptedInterfaces']['approvedBy'] == entry['actor'].user_id
            assert project.data['interfaces']['firmware'] == {'board': 'nrf52840dk/nrf52840'}
            assert len(project.data['interfaceApprovalHistory']) == 1
            scope = RunScope.model_validate(run.data['scope'])
            assert scope == entry['scope'].model_copy(update={'revision': project.context_revision})
            assert scope.actor == entry['creator'] and run.status == 'pending'
            assert len(run.data['scopeAmendments']) == 1
