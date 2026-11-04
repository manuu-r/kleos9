"""Generic answer commands cannot stand in for explicit interface approval."""
import pytest
from test_worker import worker as worker

from kleos.contracts.models import DomainError
from kleos.projects.store import Objective, Project


async def test_generic_answer_rejects_interface_prerequisite_without_scheduling_work(worker):
    runner, actor, project_id, run_id, _, specialist = worker
    await runner.process(run_id)
    with runner.projects.transaction() as session:
        run = session.get(Objective, run_id)
        interrupt_id = run.data['interruptId']
        run.data = {**run.data, 'question': {'kind': 'interface_prerequisite',
            'requestId': 'server-request', 'proposalHash': 'a'*64}}
        revision = session.get(Project, project_id).revision
    with pytest.raises(DomainError) as caught:
        runner.answer(actor, project_id, run_id, interrupt_id, 'approve interfaces', revision, 'bypass')
    assert caught.value.code == 'prerequisite_reply_required'
    with runner.projects.transaction() as session:
        run = session.get(Objective, run_id)
        assert run.status == 'waiting' and run.data.get('pendingAnswer') is None
    assert specialist.calls == 0
