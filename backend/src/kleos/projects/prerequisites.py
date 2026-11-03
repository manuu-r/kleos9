"""Explicit canonical interface approval at a quiescent objective boundary."""
import copy
import json
from datetime import UTC, datetime

from sqlalchemy import select

from kleos.artifacts.service import Artifact, NativePublication
from kleos.contracts.models import ArtifactRef, DomainError, TaskSpec
from kleos.execution.gateway import NativeJob
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import Effect, GraphRepository, execution_owner
from kleos.projects.interfaces import InterfaceProposal, _source_refs, accepted_circuit_identity, baseline
from kleos.projects.store import Membership, Objective, Project, Receipt, fingerprint

KIND = 'interface_prerequisite'


def fail(message, code='prerequisite_conflict'):
    raise DomainError(message, code)


def descriptor(record):
    return {key: record[key] for key in ('requestId', 'runId', 'round', 'missing')}


class PrerequisiteService:
    def __init__(self, projects, interfaces, repository=None, *, max_rounds=3):
        if type(max_rounds) is not int or not 1 <= max_rounds <= 4 or interfaces.projects is not projects:
            raise ValueError('Prerequisite services and proposal round bound must match')
        self.projects, self.interfaces = projects, interfaces
        self.repository = repository or GraphRepository(projects)
        self.max_rounds = max_rounds

    def _binding(self, session, project):
        rows, sources = _source_refs(session, project)
        ids = project.data.get('referenceImageIds', [])
        if not isinstance(ids, list) or len(ids) > 8 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            fail('Reference image context is invalid', 'artifact_scope_conflict')
        images = []
        for ident in ids:
            row = session.get(Artifact, ident)
            if row is None or row.project_id != project.id or row.details.get('origin') != 'reference_image':
                fail('Reference image context changed', 'artifact_scope_conflict')
            images.append(ArtifactRef(id=row.id, path=row.name, sha256=row.sha256, size=row.size,
                revision=row.revision, media_type=row.media_type).model_dump(mode='json', by_alias=True))
            rows[ident] = row
        # Every blob read is bounded and verifies its digest; no contents enter the approval record.
        if rows and self.interfaces.artifacts is None:
            fail('Reviewed artifact bytes are unavailable', 'artifact_unavailable')
        for row in rows.values():
            if len(self.interfaces.artifacts.blobs.get(row.sha256)) != row.size:
                fail('Reviewed artifact byte size changed', 'artifact_scope_conflict')
        return fingerprint({'baseline': baseline(project), 'brief': project.data.get('brief'),
            'sources': sources, 'images': images,
            'acceptedInterfaces': project.data.get('acceptedInterfaces'),
            'acceptedCircuitIdentity': project.data.get('acceptedCircuitIdentity')})

    @staticmethod
    def _quiescent(session, project, run, *, inputs=None):
        from kleos.capabilities.external import ExternalJob
        from kleos.capabilities.mcp_bridge.runner import McpSession
        from kleos.execution.protocol import LANES
        from kleos.orchestration.external_waits import ExternalWait
        for task_id, packet in (inputs or {}).items():
            task = TaskSpec.model_validate(packet.get('task'))
            if (task.id != task_id or task.capability.value in LANES
                    or run.data.get('results', {}).get(task_id, {}).get('status') != 'succeeded'):
                fail('Prepared work is outside the quiescent approval boundary', 'prerequisite_not_quiescent')
        if any(session.scalar(query.limit(1)) is not None for query in (
            select(NativeJob.id).where(NativeJob.run_id == run.id),
            select(NativePublication.id).where(NativePublication.run_id == run.id),
            select(Artifact.id).where(Artifact.project_id == project.id, Artifact.details['runId'].as_string() == run.id),
            select(Effect.id).where(Effect.run_id == run.id, Effect.status != 'completed'),
            select(ExternalWait.id).where(ExternalWait.run_id == run.id),
            select(McpSession.id).where(McpSession.run_id == run.id, McpSession.status != 'removed'),
            select(Objective.id).where(Objective.project_id == project.id, Objective.id != run.id,
                Objective.status.in_(['pending', 'running', 'waiting'])),
            select(ExternalJob.id).where(ExternalJob.project_id == project.id,
                (ExternalJob.effect_state.in_(['dispatching', 'uncertain'])) |
                (~ExternalJob.status.in_(['completed', 'failed', 'cancelled', 'expired']))),
        )):
            fail('Interface continuation requires a quiescent objective before native work', 'prerequisite_not_quiescent')

    @staticmethod
    def _pins(run):
        pins = run.data.get('runtimePins')
        if not isinstance(pins, dict) or not pins.get('native'):
            fail('Prerequisite planning requires pinned qualified native installations', 'tool_unavailable')
        return pins

    def _missing(self, session, project, run, tasks):
        lanes = {task.capability.value for task in tasks}
        pins = self._pins(run)
        missing, boards = [], []
        for lane in lanes & {'pcb', 'firmware'}:
            installed = [pin for pin in pins['native'] if pin.get('capability') == lane]
            if len(installed) != 1:
                fail('A planned interface requires its original qualified native installation', 'tool_unavailable')
            if lane == 'firmware':
                board = installed[0].get('firmwareBoard')
                if not isinstance(board, str) or not board:
                    fail('Pinned firmware installation has no qualified board', 'tool_unavailable')
                boards = [board]
                target = project.data.get('interfaces', {}).get('firmware')
                if target is None:
                    missing.append('firmware')
                elif target != {'board': board}:
                    fail('Existing firmware target does not match the pinned qualified board', 'firmware_target_required')
            else:
                if project.data.get('interfaces', {}).get('circuit') is None:
                    missing.append('circuit')
                else:
                    # Stale existing approvals require deliberate revision, not automatic replacement.
                    accepted_circuit_identity(session, project)
        return sorted(missing), boards

    def _load(self, session, scope, request_id):
        project, run = self.repository._check(session, scope)
        record = run.data.get('prerequisites', {}).get(request_id)
        if (not isinstance(record, dict) or run.data.get('prerequisiteCurrent') != request_id
                or record.get('scope') != scope.model_dump(mode='json')
                or run.data.get('initialScope') != record.get('scope')
                or record.get('contextHash') != self._binding(session, project)
                or record.get('pinsHash') != fingerprint(run.data.get('runtimePins'))):
            fail('Prerequisite request authority changed')
        return project, run, copy.deepcopy(record)

    @staticmethod
    def _save(run, record):
        if len(json.dumps(record, allow_nan=False).encode()) > 256000:
            fail('Prerequisite record exceeds its bound', 'context_too_large')
        run.data = {**run.data, 'prerequisiteCurrent': record['requestId'],
            'prerequisites': {**run.data.get('prerequisites', {}), record['requestId']: record}}

    def inspect(self, scope, tasks, results=None, task_inputs=None):
        tasks = [TaskSpec.model_validate(task) for task in tasks]
        if not any(task.capability.value in {'pcb', 'firmware'} for task in tasks):
            return None
        with self.projects.transaction() as session:
            project, run = self.repository._check(session, scope)
            missing, boards = self._missing(session, project, run, tasks)
            if not missing:
                return None
            saved = {value['id']: TaskSpec.model_validate(value) for value in run.data.get('tasks', [])}
            if any(saved.get(task.id) != task for task in tasks):
                fail('Prerequisites must follow the exact published plan')
            self._quiescent(session, project, run, inputs=task_inputs)
            current = run.data.get('prerequisiteCurrent')
            if current:
                _, _, record = self._load(session, scope, current)
                if record['missing'] != missing or record['status'] not in {'requested', 'pending', 'needs_input'}:
                    fail('Prerequisite request changed')
                return descriptor(record)
            encoded_scope = scope.model_dump(mode='json')
            if run.data.get('scope') != encoded_scope:
                fail('Prerequisite admission requires the original worker scope')
            if run.data.get('initialScope', encoded_scope) != encoded_scope or run.data.get('scopeAmendments'):
                fail('The first approval phase cannot replace its initial scope')
            record = {'runId': run.id, 'round': 1, 'missing': missing, 'qualifiedFirmwareBoards': boards,
                'scope': encoded_scope, 'contextHash': self._binding(session, project),
                'baselineHash': baseline(project), 'pinsHash': fingerprint(self._pins(run)),
                'maxRounds': self.max_rounds, 'objective': run.data['objective'],
                'status': 'requested', 'previousCandidate': None}
            record['requestId'] = fingerprint(record)
            run.data = {**run.data, 'initialScope': copy.deepcopy(encoded_scope)}
            self._save(run, record)
            self.projects._emit(session, project, 'prerequisite.requested', descriptor(record))
            return descriptor(record)

    def request(self, scope, request_id):
        with self.projects.transaction() as session:
            project, _, record = self._load(session, scope, request_id)
            packet = self.interfaces.proposal_context(session, project, scope.actor, record['objective'],
                record['previousCandidate'])
            return {**packet, 'requiredInterfaces': record['missing'],
                'qualifiedFirmwareBoards': record['qualifiedFirmwareBoards'],
                'instruction': 'Propose only requiredInterfaces; firmware board must be in qualifiedFirmwareBoards.'}

    def publish(self, scope, request_id, result):
        observations = [item for item in result.observations if item.name == 'interfaces.proposal']
        if result.status != 'succeeded' or len(observations) != 1:
            fail('Prerequisite proposal requires one typed candidate', 'invalid_result')
        candidate = InterfaceProposal.model_validate(observations[0].details['candidate'])
        encoded = candidate.model_dump(mode='json', by_alias=True)
        checksum = fingerprint(encoded)
        with self.projects.transaction() as session:
            project, run, record = self._load(session, scope, request_id)
            if record.get('proposalHash'):
                if record['proposalHash'] != checksum:
                    fail('Prerequisite proposal changed during replay', 'result_conflict')
                return copy.deepcopy(record['payload'])
            self._quiescent(session, project, run)
            contracts, identity = self.interfaces._validate(session, project, candidate)
            if set(contracts) - set(record['missing']) or (not candidate.questions and set(contracts) != set(record['missing'])):
                fail('Proposal must cover exactly the missing prerequisite interfaces', 'proposal_conflict')
            if 'firmware' in contracts and contracts['firmware']['board'] not in record['qualifiedFirmwareBoards']:
                fail('Proposal board is outside its pinned qualification', 'firmware_target_required')
            proposal_id = fingerprint({'requestId': request_id, 'candidate': checksum})
            question = ('Reply with the missing facts to refine this proposal.' if candidate.questions else
                'Review the complete interface proposal in the conversation, then reply exactly "approve interfaces". '
                'This approves configuration only, not engineering or physical behavior.')
            payload = {'kind': KIND, 'runId': run.id, 'requestId': request_id,
                'proposalId': proposal_id, 'proposalHash': checksum, 'question': question}
            record.update(status='needs_input' if candidate.questions else 'pending', candidate=encoded,
                circuitIdentity=identity, proposalId=proposal_id, proposalHash=checksum, payload=payload)
            state = copy.deepcopy(project.data.get('state', {}))
            self._conversation(state, {'id': 'prerequisite-'+proposal_id, 'role': 'assistant',
                'text': candidate.summary+'\n\n'+question+'\n\n'+json.dumps({
                    'proposal': encoded, 'resolvedCircuitIdentity': identity}, indent=2)})
            state.update(prerequisite=payload, planning={'phase': 'interfaces', 'pending': False, 'runId': run.id})
            project.advance_revision(context=False)
            project.data = {**project.data, 'state': state, 'displayStage': 0, 'status': 'review'}
            self._save(run, record)
            self.projects._emit(session, project, 'prerequisite.proposed', payload)
            return payload

    @staticmethod
    def _conversation(state, message):
        conversation = state.get('conversation', [])
        if len(conversation) >= 500:
            fail('Conversation reached its bound', 'conversation_limit')
        state['conversation'] = [*conversation, {**message, 'createdAt': datetime.now(UTC).isoformat()}]

    def wait_payload(self, scope, request_id):
        with self.projects.transaction() as session:
            _, _, record = self._load(session, scope, request_id)
            if 'payload' not in record:
                fail('Prerequisite proposal has not been published')
            return copy.deepcopy(record['payload'])

    def _pending(self, session, project):
        matches = []
        for run in session.scalars(select(Objective).where(Objective.project_id == project.id,
                Objective.status.in_(['pending', 'running', 'waiting']))):
            record = run.data.get('prerequisites', {}).get(run.data.get('prerequisiteCurrent'))
            if record and record.get('status') in {'requested', 'pending', 'needs_input'}:
                matches.append((run, record))
        if len(matches) > 1:
            fail('More than one prerequisite needs explicit resolution', 'prerequisite_ambiguous')
        return matches[0] if matches else (None, None)

    def pending(self, actor, project_id):
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, actor, project_id, write=True)
            _, record = self._pending(session, project)
            return None if record is None else {**descriptor(record), 'status': record['status'],
                **{key: record[key] for key in ('proposalId', 'proposalHash') if key in record}}

    def dispatch_message(self, actor, body, key):
        if body.get('type') != 'project.message' or body.get('payload', {}).get('runId'):
            return None
        payload = body.get('payload', {})
        with self.projects.transaction() as session:
            prior = session.scalar(select(Receipt).where(Receipt.tenant_id == actor.tenant_id,
                Receipt.user_id == actor.user_id, Receipt.key == key))
            if prior:
                if 'prerequisiteRequestId' not in prior.result:
                    return None
            else:
                project = self.projects.authorized(session, actor, payload.get('projectId'), write=True)
                _, pending = self._pending(session, project)
                if pending is None:
                    return None
        from kleos.api.interfaces import MessageRequest
        MessageRequest.model_validate(payload)  # Validate only this owned route, preserving unrelated message aliases.
        return self._reply(actor, payload.get('projectId'), body.get('expectedRevision'), key,
            text=payload.get('text'), body=body)

    def reply(self, actor, project_id, expected_revision, key, *, text, proposal_id=None, proposal_hash=None):
        body = {'type': 'project.prerequisite.reply', 'expectedRevision': expected_revision,
            'payload': {'projectId': project_id, 'text': text, 'proposalId': proposal_id, 'proposalHash': proposal_hash}}
        return self._reply(actor, project_id, expected_revision, key, text=text, body=body)

    def _reply(self, actor, project_id, expected_revision, key, *, text, body):
        if execution_owner.get() is not None:
            fail('Only an explicit user command can approve or refine prerequisites', 'approval_required')
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            fail('Provide a bounded prerequisite reply', 'invalid_request')
        if any(body.get('payload', {}).get(name) for name in ('referenceImageIds', 'selectedPartId', 'conceptVersion')):
            fail('Changed image or part context requires separate review', 'proposal_conflict')

        def operation(session):
            # Lock Project first, then all participating accounts in one deterministic order.
            # Authorizing the caller first would invert Account A/B for opposite approvers.
            project = session.scalar(select(Project).where(Project.id == project_id,
                Project.tenant_id == actor.tenant_id).with_for_update())
            member = session.get(Membership, (project_id, actor.user_id)) if project else None
            if (project is None or member is None or project.data.get('deletion') is not None
                    or project.data.get('internalPurpose') is not None):
                raise DomainError('Project not found', 'not_found', 404)
            if member.role not in {'owner', 'editor'}:
                raise DomainError('Project is read-only', 'forbidden', 403)
            if project.revision != expected_revision:
                fail('Review the current prerequisite before replying', 'revision_conflict')
            run, saved = self._pending(session, project)
            if run is None or run.status != 'waiting' or saved.get('payload') != run.data.get('question') or not run.data.get('interruptId'):
                fail('Wait for the exact prerequisite interruption before replying', 'run_conflict')
            record = copy.deepcopy(saved)
            old = RunScope.model_validate(record['scope'])
            if old.actor.tenant_id != actor.tenant_id or old.project_id != project.id or old.run_id != run.id:
                fail('Original execution actor is outside this project', 'stale_context')
            actors = {(item.user_id, item.tenant_id): item for item in (actor, old.actor)}
            for identity in sorted(actors):
                self.projects.authorize_account(session, actors[identity], write=True)
            self.projects.authorized(session, actor, project.id, write=True)
            self.projects.authorized(session, old.actor, project.id, write=True)
            if (run.data.get('scope') != record['scope'] or run.revision != old.revision
                    or run.data.get('initialScope') != record['scope'] or run.data.get('scopeAmendments')
                    or project.context_revision != old.revision
                    or self._binding(session, project) != record['contextHash']
                    or fingerprint(run.data.get('runtimePins')) != record['pinsHash']):
                fail('Prerequisite context or execution authority changed', 'stale_context')
            if datetime.now(UTC) >= old.deadline_at:
                fail('Original objective lifetime expired', 'deadline_exceeded')
            from kleos.projects import compute
            if old.compute_budget_seconds is not None and 'activeBudget' not in run.data:
                fail('Original active-work budget history is missing', 'compute_budget_conflict')
            budget = compute._budget(run, old)
            if budget and (budget.get('lease') is not None or budget['committedMs'] >= budget['limitMs']):
                fail('Wait for a closed worker grant with remaining compute', 'prerequisite_not_quiescent')
            self._quiescent(session, project, run)
            supplied = body.get('payload', {})
            if any(supplied.get(key) is not None and supplied[key] != record.get(key)
                   for key in ('proposalId', 'proposalHash')):
                fail('Reply identifies another prerequisite proposal', 'proposal_conflict')
            approval = text.strip().casefold() == 'approve interfaces'
            state = copy.deepcopy(project.data.get('state', {}))
            self._conversation(state, {'id': 'prerequisite-reply-'+fingerprint({'run': run.id, 'key': key}),
                'role': 'user', 'text': text})
            reply = {'kind': KIND, 'runId': run.id, 'requestId': record['requestId'],
                'proposalHash': record['proposalHash'], 'interruptId': run.data['interruptId'],
                'status': 'approved' if approval else 'revise'}
            if approval:
                candidate = InterfaceProposal.model_validate(record['candidate'])
                if record['status'] != 'pending':
                    fail('Resolve missing facts before approving interfaces', 'proposal_not_ready')
                _, identity = self.interfaces._validate(session, project, candidate)
                if identity != record['circuitIdentity']:
                    fail('Prerequisite circuit binding changed', 'interface_binding_stale')
                accepted = self.interfaces.accept_contracts(session, project, actor, candidate,
                    proposal_id=record['proposalId'], proposal_hash=record['proposalHash'], baseline_hash=record['baselineHash'])
                new = old.model_copy(update={'revision': project.context_revision})
                amendment = {'oldScope': old.model_dump(mode='json'), 'newScope': new.model_dump(mode='json'),
                    'requestId': record['requestId'], 'proposalHash': record['proposalHash'],
                    'interruptId': reply['interruptId'], 'approvedBy': actor.user_id, 'approvedAt': accepted['approvedAt'],
                    'pinsHash': record['pinsHash'], 'oldContextHash': record['contextHash'], 'newContextHash': self._binding(session, project)}
                amendment_id = fingerprint(amendment)
                reply['amendmentId'] = amendment_id
                record['status'] = 'approved'
                self._save(run, record)
                run.revision = new.revision
                run.data = {**run.data, 'scope': new.model_dump(mode='json'),
                    'scopeAmendments': {**run.data.get('scopeAmendments', {}), amendment_id: amendment}}
                state['interfacesAgreed'] = True
                state.pop('prerequisite', None)
                self.projects._emit(session, project, 'interfaces.approved', accepted)
            else:
                if record['round'] >= record['maxRounds']:
                    fail('Prerequisite proposal allowance is exhausted', 'prerequisite_budget_exhausted')
                record['status'] = 'revised'
                self._save(run, record)
                next_record = {key: copy.deepcopy(record[key]) for key in ('runId', 'missing', 'qualifiedFirmwareBoards',
                    'scope', 'contextHash', 'baselineHash', 'pinsHash', 'maxRounds')}
                next_record.update(round=record['round']+1, objective=run.data['objective']+'\nUser clarification: '+text,
                    status='requested', previousCandidate=record['candidate'])
                next_record['requestId'] = fingerprint(next_record)
                self._save(run, next_record)
                reply['pending'] = descriptor(next_record)
                project.advance_revision(context=False)
                state.pop('prerequisite', None)
            reply_id = fingerprint(reply)
            reply['receiptId'] = reply_id
            run.data = {**run.data, 'prerequisiteReplies': {**run.data.get('prerequisiteReplies', {}), reply_id: reply},
                'pendingAnswer': {'interruptId': reply['interruptId'], 'value': reply}}
            run.status = 'pending'
            state['planning'] = {'phase': 'interfaces', 'pending': True, 'runId': run.id}
            project.data = {**project.data, 'state': state, 'status': 'queued'}
            self.projects._emit(session, project, 'prerequisite.answered', reply)
            return {'projectId': project.id, 'runId': run.id, 'revision': project.revision,
                'prerequisiteRequestId': record['requestId']}
        return self.projects._command(actor, key, body, operation,
            replay_authorizer=lambda session, result: self.projects.authorized(session, actor, result['projectId'], write=True))

    def resolve(self, scope, payload, resume_value):
        with self.projects.transaction() as session:
            project, run = self.repository._check(session, scope)
            reply = run.data.get('prerequisiteReplies', {}).get(resume_value.get('receiptId')) if isinstance(resume_value, dict) else None
            if (reply is None or reply != resume_value or reply.get('requestId') != payload.get('requestId')
                    or reply.get('proposalHash') != payload.get('proposalHash') or reply.get('runId') != scope.run_id):
                fail('Prerequisite continuation requires its exact server reply', 'approval_required')
            if reply['status'] == 'approved':
                amendment = run.data.get('scopeAmendments', {}).get(reply.get('amendmentId'))
                if (not amendment or amendment['newScope'] != scope.model_dump(mode='json')
                        or run.data.get('initialScope') != amendment['oldScope']
                        or len(run.data.get('scopeAmendments', {})) != 1
                        or fingerprint(amendment) != reply['amendmentId']
                        or amendment['newContextHash'] != self._binding(session, project)
                        or amendment['pinsHash'] != fingerprint(run.data.get('runtimePins'))):
                    fail('Approval scope transition is missing')
                return {'status': 'approved'}
            self._load(session, scope, reply['pending']['requestId'])
            return {'status': 'revise', 'pending': copy.deepcopy(reply['pending'])}

    def validate_resume(self, old_scope, new_scope, answer, interrupts):
        old_scope, new_scope = RunScope.model_validate(old_scope), RunScope.model_validate(new_scope)
        if (old_scope.model_copy(update={'revision': new_scope.revision}) != new_scope
                or new_scope.revision <= old_scope.revision or not isinstance(answer, dict) or len(answer) != 1):
            fail('Only an exact approved revision can amend the original scope', 'run_conflict')
        interrupt_id, reply = next(iter(answer.items()))
        if not isinstance(reply, dict) or reply.get('interruptId') != interrupt_id:
            fail('Approval resume is not bound to its interruption', 'run_conflict')
        with self.projects.transaction() as session:
            project, run = self.repository._check(session, new_scope)
            amendment = run.data.get('scopeAmendments', {}).get(reply.get('amendmentId'))
            if (not amendment or fingerprint(amendment) != reply.get('amendmentId')
                    or run.data.get('initialScope') != old_scope.model_dump(mode='json')
                    or len(run.data.get('scopeAmendments', {})) != 1
                    or amendment['oldScope'] != old_scope.model_dump(mode='json')
                    or amendment['newScope'] != new_scope.model_dump(mode='json')
                    or amendment['pinsHash'] != fingerprint(run.data.get('runtimePins'))
                    or amendment['newContextHash'] != self._binding(session, project)
                    or run.data.get('prerequisiteReplies', {}).get(reply.get('receiptId')) != reply
                    or run.data.get('pendingAnswer') != {'interruptId': interrupt_id, 'value': reply}
                    or not any(item.id == interrupt_id and item.value.get('kind') == KIND
                        and item.value.get('requestId') == amendment['requestId']
                        and item.value.get('proposalHash') == amendment['proposalHash'] for item in interrupts)):
                fail('Scope amendment is not the exact durable approval continuation', 'run_conflict')
            return True
