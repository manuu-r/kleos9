"""Final Docker handshake is ingested before candidate job cleanup."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from test_native_sessions import ledger as ledger  # noqa: F401

from kleos.contracts import DomainError
from kleos.db import NativeSessionStep, Validation
from kleos.session_bridge import SessionBridge


def prepare(ledger, tmp_path):
    ledger.settings.execution_backend = 'docker'
    bridge = SessionBridge(ledger.db, ledger.settings, SimpleNamespace(control_directory=lambda job: tmp_path / job))
    step = bridge.finish('a', 'j')
    path = tmp_path / 'j/steps' / (step + '.result.json')
    return bridge, step, path


def test_final_handshake_completion_is_durable_and_replayable_before_terminal_cleanup(ledger, tmp_path):
    bridge, ident, path = prepare(ledger, tmp_path)
    path.write_text(json.dumps({'status': 'succeeded', 'result': {'status': 'succeeded', 'checks': [], 'candidate': True}}))
    path.chmod(0o644)
    assert bridge.ingest_local_finish('j')['status'] == 'succeeded'
    assert bridge.ingest_local_finish('j')['status'] == 'succeeded'
    with ledger.db.transaction(read_only=True) as session:
        step = session.get(NativeSessionStep, ident)
        assert step.status == 'succeeded' and step.completed_at is not None and step.result_hash
        assert session.scalar(select(func.count()).select_from(Validation)) == 0


@pytest.mark.parametrize('unsafe', ['symlink', 'validation_authority'])
def test_invalid_finish_delivery_cannot_complete_the_ledger_or_create_validation(ledger, tmp_path, unsafe):
    bridge, ident, path = prepare(ledger, tmp_path)
    body = {'status': 'succeeded', 'result': {'checks': []}}
    if unsafe == 'symlink':
        target = tmp_path / 'external-result.json'
        target.write_text(json.dumps(body))
        path.symlink_to(target)
    else:
        body['result']['checks'] = [{'name': 'generator_claim', 'status': 'PASS'}]
        path.write_text(json.dumps(body))
    with pytest.raises(DomainError):
        bridge.ingest_local_finish('j')
    with ledger.db.transaction(read_only=True) as session:
        assert session.get(NativeSessionStep, ident).status == 'queued'
        assert session.scalar(select(func.count()).select_from(Validation)) == 0
