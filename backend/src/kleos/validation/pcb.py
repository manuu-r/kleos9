"""Independent PCB adapter bound to accepted authority and immutable native candidates."""
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from kleos.artifacts.service import Artifact
from kleos.contracts.models import DomainError
from kleos.execution.gateway import NativeJob
from kleos.execution.pcb_contract import circuit_contract
from kleos.execution.security import daemon_socket_profile
from kleos.projects.interfaces import accepted_circuit_identity
from kleos.projects.store import fingerprint
from kleos.validation.pcb_check import MAX_FILE, MAX_TOTAL, PROTOCOL, source_supported
from kleos.validation.service import CheckerConfiguration, CheckReport

EXPECTED_CASES = {'valid_board': 'PASS', 'wrong_reference': 'FAIL', 'wrong_net': 'FAIL',
    'moved_component': 'FAIL', 'changed_outline': 'FAIL', 'clearance_violation': 'FAIL',
    'pad_position_mismatch': 'FAIL', 'pad_size_mismatch': 'FAIL', 'pad_layer_mismatch': 'FAIL',
    'unconnected_board': 'FAIL', 'erc_violation': 'FAIL', 'digest_mismatch': 'FAIL',
    'unsupported_zone': 'UNKNOWN', 'untrusted_rule_exemption': 'FAIL', 'parser_timeout': 'ERROR'}


def source_hashes():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ('pcb.py', 'pcb_check.py', 'pcb_process.py', 'Dockerfile.pcb')}


class PcbChecker:
    def __init__(self, repository, blobs, process, control_root, *, daemon_root=None):
        self.repository, self.blobs, self.process = repository, blobs, process
        self.root = Path(control_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.daemon_root = Path(daemon_root).resolve() if daemon_root else self.root

    def _authority(self, scope, subject, output):
        with self.repository.store.transaction() as session:
            project, _ = self.repository._check(session, scope)
            identity = accepted_circuit_identity(session, project)
            contract = circuit_contract(project.data.get('interfaces', {}).get('circuit'), identity['peripheralConceptIds'])
            artifact = session.get(Artifact, output.id)
            job = session.get(NativeJob, artifact.details.get('nativeJobId')) if artifact else None
            if (artifact is None or artifact.project_id != project.id or artifact.lane != 'pcb'
                    or job is None or job.project_id != project.id or job.run_id != scope.run_id
                    or job.request_hash != artifact.details.get('requestHash')
                    or fingerprint(job.request) != job.request_hash
                    or job.task_id != artifact.details.get('taskId')
                    or any({ref.path: ref.sha256 for ref in subject.sources}.get(path) != checksum
                           for path, checksum in job.source_manifest.items())
                    or job.request.get('imageDigest') != artifact.details.get('imageDigest')
                    or job.request.get('circuitContract') != contract
                    or job.request.get('circuitContractHash') != fingerprint(contract)
                    or job.request.get('acceptedCircuitIdentity') != identity):
                raise DomainError('PCB producer does not match current accepted circuit authority', 'checker_scope')
            return {'circuitContract': contract, 'circuitContractHash': fingerprint(contract),
                    'acceptedIdentityHash': identity['bindingHash']}

    async def invoke(self, scope, subject):
        if subject.project_id != scope.project_id or subject.revision != scope.revision:
            raise DomainError('PCB checker subject changed', 'scope_conflict')
        refs = subject.sources+subject.outputs
        if await self.repository.resolve_artifacts(scope, [ref.id for ref in refs]) != refs:
            raise DomainError('PCB checker artifact binding changed', 'artifact_conflict')
        with self.repository.store.transaction() as session:
            self.repository._check(session, scope)
            outputs = [ref for ref in subject.outputs if session.get(Artifact, ref.id).lane == 'pcb']
        if len(outputs) != 1 or outputs[0].path != 'outputs/routed.kicad_pcb':
            raise DomainError('PCB policy checks exactly one routed board', 'checker_scope')
        authority = self._authority(scope, subject, outputs[0])
        selected = [ref for ref in subject.sources if source_supported(ref.path)]+outputs
        if not {'board.kicad_pcb', 'circuit.kicad_sch'} <= {ref.path for ref in selected}:
            raise DomainError('PCB policy requires exact schematic and board sources', 'checker_input')
        if len(selected) > 256 or len({ref.path for ref in selected}) != len(selected):
            raise DomainError('PCB checker source closure is ambiguous', 'checker_input')
        ident = 'pcb-check-'+uuid4().hex
        request = {'protocol': PROTOCOL, 'executionId': ident, 'subjectHash': subject.subject_hash,
                   **authority, 'files': []}
        folder = Path(tempfile.mkdtemp(prefix='pcb-check-', dir=self.root))
        folder.chmod(0o755)
        try:
            total = 0
            for ref in selected:
                content = self.blobs.get(ref.sha256)
                total += len(content)
                if len(content) != ref.size or len(content) > MAX_FILE or total > MAX_TOTAL:
                    raise DomainError('PCB checker byte limits exceeded', 'checker_input')
                target = folder/ref.sha256
                target.write_bytes(content)
                target.chmod(0o644)
                request['files'].append({'artifactId': ref.id, 'path': ref.path, 'size': ref.size, 'sha256': ref.sha256})
            (folder/'request.json').write_text(json.dumps(request, allow_nan=False))
            (folder/'request.json').chmod(0o644)
            result = await self.process.run(self.daemon_root/folder.name, ident, project_id=scope.project_id,
                assert_active=lambda: self.repository.assert_active(scope), deadline_at=scope.deadline_at)
            await self.repository.assert_active(scope)
            # Recheck accepted authority after IO, before the generic publication fence.
            if self._authority(scope, subject, outputs[0]) != authority:
                raise DomainError('PCB authority changed during checking', 'stale_context')
            if (result.get('protocol') != PROTOCOL or result.get('subjectHash') != subject.subject_hash
                    or result.get('executionId') != ident
                    or result.get('checkerSourceSha256') != source_hashes()['pcb_check.py']
                    or result.get('status') not in {'PASS', 'FAIL', 'UNKNOWN', 'ERROR'}):
                raise DomainError('Independent PCB result binding is invalid', 'checker_output')
            return CheckReport(subject_hash=subject.subject_hash, execution_id=ident, status=result['status'],
                summary=result.get('reason') or 'Independent PCB board rules; physical acceptance remains unavailable',
                measurements={**result.get('measurements', {}), 'acceptedIdentityHash': authority['acceptedIdentityHash'],
                              'circuitContractHash': authority['circuitContractHash'], 'physicalAcceptance': False})
        finally:
            shutil.rmtree(folder)


def install_pcb_checker(registry, adapter, evidence_path):
    path = Path(evidence_path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 2*1024**2:
                raise ValueError('Invalid evidence file')
            raw = stream.read(2*1024**2+1)
    except (OSError, ValueError):
        raise DomainError('PCB checker qualification is unavailable', 'checker_unqualified') from None
    if len(raw) > 2*1024**2:
        raise DomainError('PCB checker qualification exceeds its bound', 'checker_unqualified')
    try:
        evidence = json.loads(raw)
        review = evidence.get('independentReview', {})
        reviewed = datetime.fromisoformat(review.get('reviewedAt', ''))
        payload = {key: value for key, value in evidence.items() if key != 'independentReview'}
        valid = (evidence.get('schemaVersion') == 'kleos-pcb-check-qualification-v1'
            and evidence.get('qualified') is True and evidence.get('imageId') == adapter.process.image
            and evidence.get('protocol') == PROTOCOL and evidence.get('sourceOverride') is False
            and evidence.get('deployment') == 'local' and evidence.get('containerChecksPassed') is True
            and evidence.get('networkPolicy') == 'docker-desktop-daemon-seccomp'
            and evidence.get('daemonSeccompSha256') == fingerprint(daemon_socket_profile())
            and evidence.get('watchdogSeconds') == 175 and adapter.process.timeout == 180
            and evidence.get('resources') == {'cpus': 2, 'memoryBytes': 2*1024**3, 'scratchBytes': 256*1024**2, 'pids': 64}
            and isinstance(evidence.get('qualifiedBy'), str) and 1 <= len(evidence['qualifiedBy'].strip()) <= 200
            and evidence.get('sourceHashes') == source_hashes()
            and evidence.get('caseStatuses') == EXPECTED_CASES
            and isinstance(review.get('reviewer'), str) and 1 <= len(review['reviewer'].strip()) <= 200
            and reviewed.tzinfo is not None and review.get('evidenceSha256') == fingerprint(payload)
            and review.get('approved') is True and review['reviewer'] != evidence.get('qualifiedBy'))
        if not valid:
            raise ValueError('Qualification mismatch')
    except (ValueError, TypeError, AttributeError, KeyError):
        raise DomainError('PCB checker needs matching exact-image evidence and independent review', 'checker_unqualified') from None
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', adapter.process.image):
        raise DomainError('PCB checker image is not pinned', 'checker_unqualified')
    config = CheckerConfiguration(key='pcb.board-rules', version='1', qualified=True,
        installation_hash=fingerprint({'imageId': adapter.process.image, 'qualification': fingerprint(evidence),
                                       'protocol': PROTOCOL, 'timeoutSeconds': adapter.process.timeout}),
        timeout_seconds=adapter.process.timeout)
    registry.register(config, adapter.invoke)
    return config
