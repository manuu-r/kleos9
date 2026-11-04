"""Combined installations must retain each lane's own checker instance."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_validation_tools import fixture as fixture

from kleos.capabilities.registry import CapabilityRegistry
from kleos.contracts.models import TaskSpec
from kleos.infrastructure.validation import configure_validation
from kleos.validation.cad import CadChecker
from kleos.validation.exports import ApplicationChecker, FirmwareChecker
from kleos.validation.policies import LANE_CHECKS
from kleos.validation.service import CheckReport, ValidationInput


@pytest.mark.parametrize('lanes', [('firmware',), ('application',), ('firmware', 'application')])
async def test_cad_callback_does_not_capture_later_export_checker(fixture, monkeypatch, lanes):
    store, scope, artifacts, refs, settings = fixture
    outputs = list(refs[1:3])
    for lane in lanes:
        report = Path(__file__).parents[1]/f'src/kleos/validation/qualification/export-check-{lane}-exact-image.json'
        evidence = json.loads(report.read_bytes())
        setattr(settings, lane+'_checker_installation', {
            'imageDigest': evidence['imageId'], 'qualificationReference': str(report)})
        task = TaskSpec(id=lane, capability=lane, objective='Inert wiring fixture')
        names = ['outputs/firmware.elf', 'outputs/firmware.bin'] if lane == 'firmware' else ['outputs/companion-web.zip']
        outputs.extend(await artifacts.publish_native(scope, task,
            [{'path': name, 'role': 'output', 'content': b'Inert wiring fixture'} for name in names],
            {'nativeJobId': 'fixture-'+lane, 'imageDigest': 'c'*64, 'requestHash': 'd'*64}))
    called = []
    async def observe(self, selected_scope, subject):
        called.append((type(self), [ref.path for ref in subject.outputs]))
        assert selected_scope == scope
        return CheckReport(subject_hash=subject.subject_hash, status='UNKNOWN',
            execution_id='fixture-'+type(self).__name__, summary='Wiring only; no check executed')
    for adapter in (CadChecker, FirmwareChecker, ApplicationChecker):
        monkeypatch.setattr(adapter, 'invoke', observe)
    wiring = configure_validation(settings, store, artifacts, CapabilityRegistry(), docker=SimpleNamespace())
    subject = ValidationInput(candidate_id='fixture', subject_hash='a'*64, project_id=scope.project_id,
        revision=scope.revision, sources=[refs[0]], outputs=outputs)
    await wiring.registry.checker(LANE_CHECKS['cad']).invoke(scope, subject)
    assert called == [(CadChecker, ['outputs/base.step', 'outputs/base.stl'])]
    for lane in lanes:
        await wiring.registry.checker(LANE_CHECKS[lane]).invoke(scope, subject)
    assert [kind for kind, _ in called] == [CadChecker, *[
        FirmwareChecker if lane == 'firmware' else ApplicationChecker for lane in lanes]]
