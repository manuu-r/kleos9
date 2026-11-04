"""Interface proposals are reviewable candidates; only a user command accepts them."""
import copy
import json
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator
from sqlalchemy import select

from kleos.contracts.models import ArtifactRef, DomainError, WireModel
from kleos.execution.pcb_contract import circuit_contract
from kleos.projects.graph_repository import GraphRepository, execution_owner
from kleos.projects.store import Objective, fingerprint
from kleos.validation.simulation.contract import SimulationContract
from kleos.validation.simulation.plan import plan_simulation


class CircuitReference(WireModel):
    reference: str = Field(pattern=r'^[A-Z]+[1-9][0-9]{0,5}$')
    value: str = Field(min_length=1, max_length=128)
    footprint: str = Field(pattern=r'^[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+$')
    pins: dict[str, str] = Field(min_length=1, max_length=128)


class CircuitConstraints(WireModel):
    max_width_mm: float = Field(ge=.5, le=500, allow_inf_nan=False, strict=True)
    max_height_mm: float = Field(ge=.5, le=500, allow_inf_nan=False, strict=True)
    min_track_width_mm: float = Field(ge=.05, le=10, allow_inf_nan=False, strict=True)
    min_clearance_mm: float = Field(ge=.05, le=10, allow_inf_nan=False, strict=True)
    copper_layers: list[str] = Field(min_length=2, max_length=2)


class CircuitProposal(WireModel):
    references: list[CircuitReference] = Field(min_length=1, max_length=128)
    concept_references: dict[str, str] = Field(max_length=128)
    constraints: CircuitConstraints


class FirmwareProposal(WireModel):
    # Choosing a board never qualifies its toolchain; native dispatch checks installation evidence.
    board: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_][A-Za-z0-9_-]*(/[A-Za-z0-9_][A-Za-z0-9_-]*){0,2}$')


class InterfaceContracts(WireModel):
    circuit: CircuitProposal | None = None
    firmware: FirmwareProposal | None = None
    # Explicit ideal-circuit simulation bindings; advisory evidence only, never acceptance.
    simulation: SimulationContract | None = None


class CircuitIdentityProposal(WireModel):
    mode: Literal['approved_concept', 'imported_no_concept'] = 'approved_concept'
    source_artifact_ids: list[str] = Field(default_factory=list, max_length=256)

    @model_validator(mode='after')
    def declared_sources(self):
        ids = self.source_artifact_ids
        if len(set(ids)) != len(ids) or any(not value or len(value) > 100 for value in ids):
            raise ValueError('Imported source identities must be unique and bounded')
        if self.mode == 'imported_no_concept' and not ids:
            raise ValueError('No-concept routing requires explicitly identified imported sources')
        if self.mode == 'approved_concept' and ids:
            raise ValueError('Imported identity cannot replace approved concept mapping')
        return self


class InterfaceProposal(WireModel):
    summary: str = Field(min_length=1, max_length=2000)
    interfaces: InterfaceContracts = Field(default_factory=InterfaceContracts)
    questions: list[str] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    source_ids: list[str] = Field(default_factory=list, max_length=256)
    circuit_identity: CircuitIdentityProposal = Field(default_factory=CircuitIdentityProposal)

    @model_validator(mode='after')
    def bounded(self):
        if any(not value.strip() or len(value) > 1000 for value in self.questions + self.limitations):
            raise ValueError('Questions and limitations must be bounded nonempty text')
        if any(not value or len(value) > 128 for value in self.source_ids):
            raise ValueError('Source identities must be bounded')
        if not any((self.interfaces.circuit, self.interfaces.firmware, self.interfaces.simulation, self.questions)):
            raise ValueError('Propose a scoped interface or ask for missing context')
        if self.circuit_identity.mode == 'imported_no_concept' and self.interfaces.circuit is None:
            raise ValueError('An imported identity declaration requires a complete proposed circuit')
        if len(json.dumps(self.model_dump(mode='json'))) > 48000:
            raise ValueError('Interface proposal is too large')
        return self


def baseline(project):
    return fingerprint({key: project.data.get(key) for key in
        ('requirements', 'interfaces', 'approvedDesign', 'sourceManifest')})


def _source_refs(session, project):
    from kleos.artifacts.service import Artifact
    manifest = project.data.get('sourceManifest', {})
    if not isinstance(manifest, dict) or len(manifest) > 256 or len(set(manifest.values())) != len(manifest):
        raise DomainError('Imported source closure is invalid', 'artifact_scope_conflict')
    rows, refs = {}, []
    for path, ident in sorted(manifest.items()):
        row = session.get(Artifact, ident)
        if row is None or row.project_id != project.id or row.name != path:
            raise DomainError('Imported source closure is not current project data', 'artifact_scope_conflict')
        rows[ident] = row
        refs.append(ArtifactRef(id=row.id, path=row.name, sha256=row.sha256, revision=row.revision,
            media_type=row.media_type, size=row.size).model_dump(mode='json', by_alias=True))
    return rows, refs


def _import_identity(session, project, source_ids, artifacts=None):
    if project.data.get('approvedDesign') is not None:
        raise DomainError('An approved concept cannot be downgraded to no-concept routing', 'interface_mapping_invalid')
    rows, refs = _source_refs(session, project)
    if not source_ids or len(set(source_ids)) != len(source_ids) or not set(source_ids).issubset(rows):
        raise DomainError('Imported identity must name current project sources', 'artifact_scope_conflict')
    boards = [rows[ident] for ident in source_ids if PurePosixPath(rows[ident].name).suffix.lower() == '.kicad_pcb'
        and rows[ident].details.get('origin') == 'user_upload']
    if len(boards) != 1:
        raise DomainError('Explicitly select exactly one imported KiCad board', 'imported_board_required')
    if artifacts is not None:
        # This is a source-format identification check, not PCB validation or execution.
        raw = artifacts.blobs.get(boards[0].sha256)
        if len(raw) != boards[0].size or not re.match(rb'^\s*\(kicad_pcb(?:\s|\()', raw):
            raise DomainError('Imported board bytes do not identify a KiCad PCB source', 'imported_board_invalid')
    return {'mode': 'imported_no_concept', 'peripheralConceptIds': [], 'sourceArtifacts': refs,
        'importedSourceIds': sorted(source_ids), 'sourceManifestHash': fingerprint(project.data.get('sourceManifest', {})),
        'requirementsHash': fingerprint(project.data.get('requirements', {})),
        'briefHash': fingerprint(project.data.get('brief', ''))}


def accepted_circuit_identity(session, project):
    """Read accepted identity authority inside the caller's authorized project transaction.

    Empty concept IDs are valid for imports only after explicit approval of exact
    source bytes. The native adapter must still consume those sources and validate
    exported circuit correspondence independently.
    """
    circuit = project.data.get('interfaces', {}).get('circuit')
    approved = project.data.get('approvedDesign')
    record = project.data.get('acceptedCircuitIdentity')
    if record is not None and (not isinstance(record, dict)
            or any(not isinstance(record.get(key), str) or not record[key] for key in
                ('schemaVersion', 'mode', 'circuitHash', 'proposalId', 'proposalHash', 'approvedBy', 'approvedAt'))
            or type(record.get('revision')) is not int or not 1 <= record['revision'] <= project.context_revision
            or type(record.get('sourceContextRevision')) is not int
            or not 1 <= record['sourceContextRevision'] < record['revision']):
        raise DomainError('Accepted circuit identity record is incomplete', 'interface_binding_stale')
    if approved is not None:
        ids = approved.get('peripheralConceptIds') if isinstance(approved, dict) else None
        try:
            circuit_contract(circuit, ids)
        except ValueError as error:
            raise DomainError(str(error), 'interface_mapping_invalid') from None
        if project.data.get('state', {}).get('approvedDesignStatus', 'current') != 'current':
            raise DomainError('Approved concept is no longer current', 'design_not_current')
        identity = {'mode': 'approved_concept', 'peripheralConceptIds': copy.deepcopy(ids),
            'sourceArtifacts': [], 'importedSourceIds': [], 'conceptHash': fingerprint(approved)}
        if record and (record.get('mode') != 'approved_concept' or record.get('conceptHash') != identity['conceptHash']
                or record.get('circuitHash') != fingerprint(circuit)):
            raise DomainError('Accepted circuit identity changed with its concept', 'interface_binding_stale')
    else:
        if not isinstance(record, dict) or record.get('mode') != 'imported_no_concept':
            raise DomainError('Imported PCB requires explicit no-concept interface approval', 'interface_approval_required')
        identity = _import_identity(session, project, record.get('importedSourceIds', []))
        if any(record.get(key) != value for key, value in identity.items()):
            raise DomainError('Imported source identity changed after approval', 'interface_binding_stale')
        if (record.get('schemaVersion') != 'circuit-identity-v1' or record.get('circuitHash') != fingerprint(circuit)
                or not record.get('proposalId') or not record.get('proposalHash') or not record.get('approvedBy')):
            raise DomainError('Imported circuit approval is incomplete or changed', 'interface_binding_stale')
        history = project.data.get('interfaceApprovalHistory', [])
        receipt = next((item for item in history if item.get('proposalId') == record['proposalId']), None)
        if (receipt is None or receipt.get('circuitIdentity') != record or receipt.get('interfaces', {}).get('circuit') != circuit
                or receipt.get('proposalHash') != record['proposalHash'] or receipt.get('approvedBy') != record['approvedBy']):
            raise DomainError('Imported circuit lacks its matching user approval record', 'interface_approval_required')
        try:
            circuit_contract(circuit, [])
        except ValueError as error:
            raise DomainError(str(error), 'interface_mapping_invalid') from None
    identity['circuitHash'] = fingerprint(circuit)
    if record:
        identity['approval'] = {key: record[key] for key in ('proposalId', 'proposalHash', 'approvedBy', 'approvedAt', 'revision')}
    return {**identity, 'bindingHash': fingerprint(identity)}


class InterfaceService:
    def __init__(self, projects, artifacts=None):
        self.projects, self.artifacts = projects, artifacts

    def solve(self, actor, project_id, expected_revision, key, *, objective=None, reference_image_ids=None):
        text = objective or 'Propose only the shared circuit and firmware interfaces needed for this project.'
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise DomainError('Provide a bounded interface objective', 'invalid_request', 422)

        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            if project.revision != expected_revision:
                raise DomainError('Project changed before interface planning', 'revision_conflict')
            image_ids = reference_image_ids or []
            if (len(image_ids) > 8 or len(image_ids) != len(set(image_ids))
                    or not set(image_ids).issubset(project.data.get('referenceImageIds', []))):
                raise DomainError('Image is not in this project context', 'artifact_scope_conflict')
            active = session.scalar(select(Objective).where(Objective.project_id == project.id,
                Objective.revision == project.context_revision, Objective.status.in_(['pending', 'running', 'waiting'])))
            if active:
                raise DomainError('Finish or cancel the current objective before proposing interfaces', 'run_conflict')
            project.advance_revision()
            run = Objective(id=str(uuid4()), project_id=project.id, revision=project.context_revision,
                status='pending', data={'kind': 'project.solveInterfaces', 'objective': text,
                    'submittedBy': actor.user_id, 'createdAt': datetime.now(UTC).isoformat(), 'tasks': [],
                    'modelPolicySnapshot': copy.deepcopy(project.data.get('modelPolicy', {})),
                    'toolPolicySnapshot': copy.deepcopy({'policy': project.data.get('state', {}).get('toolPolicy', {}),
                        'bindings': project.data.get('toolPolicyBindings', {})}),
                    'interfaceRequest': {'baselineHash': baseline(project),
                        'previousCandidate': copy.deepcopy(project.data.get('state', {}).get('interfaceProposal', {}).get('candidate'))}})
            session.add(run)
            state = copy.deepcopy(project.data.get('state', {}))
            prior = state.get('interfaceProposal')
            if prior:
                prior['status'] = 'superseded'
            if objective:
                conversation = list(state.get('conversation', []))
                if len(conversation) >= 500:
                    raise DomainError('Conversation reached its bound', 'conversation_limit')
                state['conversation'] = [*conversation, {'id': 'interfaces-request-' + run.id,
                    'role': 'user', 'text': objective, 'referenceImageIds': image_ids, 'createdAt': datetime.now(UTC).isoformat()}]
            state['planning'] = {'phase': 'interfaces', 'pending': True, 'runId': run.id}
            project.data = {**project.data, 'state': state, 'status': 'queued'}
            self.projects._emit(session, project, 'objective.submitted', {'runId': run.id, 'kind': 'project.solveInterfaces'})
            return {'projectId': project.id, 'runId': run.id, 'revision': project.revision}
        return self.projects._command(actor, key, {'type': 'project.solveInterfaces', 'projectId': project_id,
            'expectedRevision': expected_revision, 'objective': text, 'referenceImageIds': reference_image_ids or []}, operation)

    def request(self, scope):
        """Bound source text is data only. Files are authorized and never executed."""
        with self.projects.transaction() as session:
            project, run = GraphRepository(self.projects)._check(session, scope)
            if run.data.get('kind') != 'project.solveInterfaces' or 'interfaceRequest' not in run.data:
                raise DomainError('Objective is not interface planning', 'run_conflict')
            if run.data['interfaceRequest']['baselineHash'] != baseline(project):
                raise DomainError('Interface baseline changed', 'stale_context')
            return self.proposal_context(session, project, scope.actor, run.data['objective'],
                run.data['interfaceRequest'].get('previousCandidate'))

    def proposal_context(self, session, project, actor, objective, previous_candidate=None):
        """Shared authorized bounded source packet; caller owns objective authority."""
        source_data, remaining = [], 24000
        for path, ident in sorted(project.data.get('sourceManifest', {}).items()):
            item = {'id': ident, 'path': path, 'textIncluded': False}
            if self.artifacts is not None:
                row = self.artifacts._authorized(session, actor, ident, project.id)
                if row.name != path:
                    raise DomainError('Imported source path changed', 'artifact_scope_conflict')
                item.update(sha256=row.sha256, revision=row.revision)
                if (row.size <= 8000 and row.size <= remaining and
                        PurePosixPath(path).suffix.lower() in {'.json', '.yaml', '.yml', '.txt', '.md', '.c', '.h', '.conf',
                            '.dts', '.overlay', '.kicad_pcb', '.kicad_sch', '.kicad_pro'}):
                    raw = self.artifacts.blobs.get(row.sha256)
                    if len(raw) != row.size:
                        raise DomainError('Imported source size changed', 'artifact_conflict')
                    try:
                        item['text'] = raw.decode('utf-8')
                        item['textIncluded'] = True
                        remaining -= len(raw)
                    except UnicodeDecodeError:
                        pass
            source_data.append(item)
            if len(source_data) > 256:
                raise DomainError('Imported source context is too large', 'context_too_large')
        return {'objective': objective, 'baselineHash': baseline(project),
            'previousCandidate': copy.deepcopy(previous_candidate), 'importedSources': source_data}

    def _validate(self, session, project, candidate):
        contracts = candidate.interfaces.model_dump(mode='json', by_alias=True, exclude_none=True)
        identity = None
        if 'circuit' in contracts:
            if candidate.circuit_identity.mode == 'imported_no_concept':
                if self.artifacts is None:
                    raise DomainError('Imported board verification requires scoped artifact storage', 'storage_unavailable', 503)
                identity = _import_identity(session, project, candidate.circuit_identity.source_artifact_ids, self.artifacts)
            else:
                approved = project.data.get('approvedDesign')
                identity = {'mode': 'approved_concept',
                    'peripheralConceptIds': approved.get('peripheralConceptIds') if isinstance(approved, dict) else None,
                    'sourceArtifacts': [], 'importedSourceIds': [], 'conceptHash': fingerprint(approved)}
            try:
                circuit_contract(contracts['circuit'], identity['peripheralConceptIds'])
            except ValueError as error:
                raise DomainError(str(error), 'interface_mapping_invalid', 422) from None
            if (identity['mode'] == 'approved_concept' and
                    project.data.get('state', {}).get('approvedDesignStatus', 'current') != 'current'):
                raise DomainError('Confirm the current concept before proposing its circuit', 'design_not_current')
            identity['circuitHash'] = fingerprint(contracts['circuit'])
        if 'simulation' in contracts:
            circuit = contracts.get('circuit') or project.data.get('interfaces', {}).get('circuit')
            if circuit is None:
                raise DomainError('Simulation bindings require an accepted or simultaneously proposed circuit',
                                  'interface_mapping_invalid', 422)
            plan = plan_simulation(circuit, contracts['simulation'])
            if not plan.ready:
                raise DomainError('Simulation bindings cannot be compiled: ' + ' '.join(plan.reasons),
                                  'interface_mapping_invalid', 422)
        sources = {'brief', *project.data.get('sourceManifest', {}).values(), *project.data.get('referenceImageIds', [])}
        if project.data.get('approvedDesign'):
            sources.add('approvedDesign')
        if not set(candidate.source_ids).issubset(sources):
            raise DomainError('Proposal cites context outside this project', 'artifact_scope_conflict')
        return contracts, identity

    def publish(self, scope, result):
        observations = [item for item in result.observations if item.name == 'interfaces.proposal']
        if result.status != 'succeeded' or len(observations) != 1:
            raise DomainError('Interface planning did not return a typed proposal', 'invalid_result')
        candidate = InterfaceProposal.model_validate(observations[0].details['candidate'])
        encoded = candidate.model_dump(mode='json', by_alias=True)
        digest = fingerprint(encoded)
        with self.projects.transaction() as session:
            project, run = GraphRepository(self.projects)._check(session, scope)
            if run.data.get('interfacePublicationHash'):
                if run.data['interfacePublicationHash'] != digest:
                    raise DomainError('Interface publication changed during replay', 'result_conflict')
                return
            if run.data.get('kind') != 'project.solveInterfaces' or run.data['interfaceRequest']['baselineHash'] != baseline(project):
                raise DomainError('Interface context changed before publication', 'stale_context')
            _, identity = self._validate(session, project, candidate)
            state = copy.deepcopy(project.data.get('state', {}))
            proposal = {'id': run.id, 'hash': digest, 'contextRevision': scope.revision,
                'baselineHash': baseline(project), 'status': 'needs_input' if candidate.questions else 'pending',
                'candidate': encoded, 'circuitIdentity': identity, 'engineeringValidated': False}
            state['interfaceProposal'] = proposal
            conversation = list(state.get('conversation', []))
            if len(conversation) >= 500:
                raise DomainError('Conversation reached its bound', 'conversation_limit')
            instruction = ('Resolve the listed questions and request a new interface proposal.' if candidate.questions else
                'Review the complete proposal below. Reply exactly "approve interfaces" to accept these configuration choices. '
                'This does not approve engineering outputs or prove electrical, firmware, or physical behavior.')
            if identity and identity['mode'] == 'imported_no_concept':
                instruction += (' This explicitly binds routing to the listed imported PCB source hashes without a visual concept. '
                    'No approved concept is created, removed, or bypassed; the imported board still needs independent checking.')
            conversation.append({'id': 'interfaces-' + run.id, 'role': 'assistant',
                'text': candidate.summary + '\n\n' + instruction + '\n\n' + json.dumps(
                    {'proposal': encoded, 'resolvedCircuitIdentity': identity}, indent=2),
                'createdAt': datetime.now(UTC).isoformat()})
            state['conversation'] = conversation
            state['planning'] = {'phase': 'interfaces', 'pending': False, 'runId': run.id}
            # A fresh UI revision prevents an approval sent against an older visible proposal.
            project.advance_revision(context=False)
            project.data = {**project.data, 'state': state, 'status': 'review', 'displayStage': 0}
            run.data = {**run.data, 'interfacePublicationHash': digest, 'interfaceCandidate': encoded,
                'interfaceCircuitIdentity': identity}
            self.projects._emit(session, project, 'interfaces.proposed', {'proposalId': run.id, 'proposalHash': digest})

    def approve(self, actor, project_id, expected_revision, key, *, proposal_id=None, proposal_hash=None, message=False):
        if execution_owner.get() is not None:
            raise DomainError('Only an explicit user command may accept interfaces', 'approval_required')

        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            if project.revision != expected_revision:
                raise DomainError('Proposal changed; review it again before approval', 'revision_conflict')
            state = copy.deepcopy(project.data.get('state', {}))
            proposal = state.get('interfaceProposal', {})
            if proposal.get('status') != 'pending':
                raise DomainError('There is no complete pending interface proposal', 'proposal_not_ready')
            if not message and (proposal_id != proposal['id'] or proposal_hash != proposal['hash']):
                raise DomainError('Approval must identify the exact reviewed proposal', 'proposal_conflict')
            if (proposal['contextRevision'] != project.context_revision or proposal['baselineHash'] != baseline(project)
                    or fingerprint(proposal['candidate']) != proposal['hash']):
                raise DomainError('Proposal source context changed', 'stale_context')
            run = session.get(Objective, proposal['id'])
            if (run is None or run.project_id != project.id or run.status != 'succeeded'
                    or run.revision != project.context_revision or run.data.get('kind') != 'project.solveInterfaces'
                    or run.data.get('interfacePublicationHash') != proposal['hash']
                    or fingerprint(run.data.get('interfaceCandidate')) != proposal['hash']):
                raise DomainError('Wait for the current proposal run to finish successfully before approval', 'run_inactive')
            candidate = InterfaceProposal.model_validate(proposal['candidate'])
            contracts, identity = self._validate(session, project, candidate)
            if identity != proposal.get('circuitIdentity') or identity != run.data.get('interfaceCircuitIdentity'):
                raise DomainError('Proposal source identity changed before approval', 'interface_binding_stale')
            if candidate.questions or not contracts:
                raise DomainError('Resolve proposal questions before approval', 'proposal_not_ready')
            accepted = self.accept_contracts(session, project, actor, candidate,
                proposal_id=proposal['id'], proposal_hash=proposal['hash'], baseline_hash=proposal['baselineHash'])
            proposal['status'] = 'approved'
            state['interfacesAgreed'] = True
            if message:
                if len(state.get('conversation', [])) >= 500:
                    raise DomainError('Conversation reached its bound', 'conversation_limit')
                state['conversation'] = [*state.get('conversation', []),
                    {'id': 'interfaces-approval-' + proposal['id'], 'role': 'user', 'text': 'approve interfaces'}]
            project.data = {**project.data, 'state': state, 'status': 'ready', 'displayStage': 3}
            self.projects._emit(session, project, 'interfaces.approved', accepted)
            return {'projectId': project.id, 'revision': project.revision}
        return self.projects._command(actor, key, {'type': 'project.approveInterfaces', 'projectId': project_id,
            'expectedRevision': expected_revision, 'proposalId': proposal_id, 'proposalHash': proposal_hash,
            'explicitMessage': message}, operation)

    def accept_contracts(self, session, project, actor, candidate, *, proposal_id, proposal_hash, baseline_hash):
        """Canonical write after a caller verifies its exact explicit approval boundary."""
        if execution_owner.get() is not None:
            raise DomainError('Only an explicit user command may accept interfaces', 'approval_required')
        if baseline(project) != baseline_hash or fingerprint(candidate.model_dump(mode='json', by_alias=True)) != proposal_hash:
            raise DomainError('Interface approval binding changed', 'proposal_conflict')
        contracts, identity = self._validate(session, project, candidate)
        if candidate.questions or not contracts:
            raise DomainError('Resolve proposal questions before approval', 'proposal_not_ready')
        accepted = {'proposalId': proposal_id, 'proposalHash': proposal_hash,
            'approvedBy': actor.user_id, 'approvedAt': datetime.now(UTC).isoformat(),
            'interfaces': contracts, 'sourceContextRevision': project.context_revision,
            'baselineHash': baseline_hash, 'engineeringValidated': False}
        history = list(project.data.get('interfaceApprovalHistory', []))
        if len(history) >= 256:
            raise DomainError('Interface approval history reached its bound', 'history_limit')
        project.advance_revision()
        accepted['revision'] = project.context_revision
        if identity is not None:
            binding = {**identity, 'schemaVersion': 'circuit-identity-v1',
                **{key: accepted[key] for key in ('proposalId', 'proposalHash', 'approvedBy', 'approvedAt', 'revision',
                    'sourceContextRevision')}}
            accepted['circuitIdentity'] = binding
            project.data = {**project.data, 'acceptedCircuitIdentity': binding}
        project.data = {**project.data, 'interfaces': {**project.data.get('interfaces', {}), **contracts},
            'acceptedInterfaces': accepted, 'interfaceApprovalHistory': [*history, accepted]}
        return accepted
