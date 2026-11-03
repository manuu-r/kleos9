"""Transactional capture drafts; only an explicit user command approves a concept."""
import copy
import math
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from kleos.artifacts.service import Artifact
from kleos.capabilities.design.models import CaptureResult, ConceptScene, Question
from kleos.contracts.models import DomainError
from kleos.projects.graph_repository import GraphRepository, execution_owner
from kleos.projects.store import Membership, Objective, Project, fingerprint
from kleos.projects.workbench_edits import validate_user_requirements


class DesignService:
    def __init__(self, projects):
        self.projects = projects

    def _state(self, project):
        state = copy.deepcopy(project.data.get('state', {}))
        state.setdefault('conversation', [{'id': 'brief', 'role': 'user', 'text': project.data['brief']}])
        return state

    def _images(self, session, project, ids):
        if len(ids) > 8 or len(set(ids)) != len(ids):
            raise DomainError('Invalid image selection', 'invalid_request', 422)
        for image_id in ids:
            row = session.get(Artifact, image_id)
            if (row is None or row.project_id != project.id or row.details.get('origin') != 'reference_image'
                    or image_id not in project.data.get('referenceImageIds', [])):
                raise DomainError('Reference image is not in this project', 'artifact_scope_conflict')

    def _queue(self, session, project, actor, state, payload):
        self._images(session, project, payload.get('referenceImageIds', []))
        selected = payload.get('selectedPartId')
        concept = state.get('concept', {})
        if selected:
            if payload.get('conceptVersion') != concept.get('version'):
                raise DomainError('Concept changed before the part redo', 'concept_conflict')
            scene = ConceptScene.model_validate(concept.get('scene'))
            if selected not in {part.id for part in scene.parts}:
                raise DomainError('Selected concept part does not exist', 'part_not_found', 404)
        for run in session.scalars(select(Objective).where(Objective.project_id == project.id,
                Objective.status.in_(['pending', 'running', 'waiting']))):
            if run.data.get('kind') == 'project.capture':
                run.status = 'cancelled'
                self.projects._emit(session, project, 'objective.cancelled', {'runId': run.id, 'reason': 'new_capture_input'})
        project.advance_revision()
        run_id = str(uuid4())
        text = payload.get('text') or 'Develop the requested concept from the original brief and current project context.'
        state['planning'] = {'phase': 'capture', 'pending': True, 'runId': run_id}
        state['confirmed'] = False
        state['redoPartId'] = selected
        state['clarification'] = {**state.get('clarification', {}), 'readyToConfirm': False, 'needsReview': True}
        request = {'selectedPartId': selected, 'conceptVersion': concept.get('version'),
            'baselineConcept': copy.deepcopy(concept), 'baselineProposal': copy.deepcopy(state.get('proposal', {})),
            'conversation': copy.deepcopy(state['conversation'][-40:]),
            'answers': copy.deepcopy(state.get('clarification', {}).get('answers', {})),
            'text': text, 'referenceImageIds': payload.get('referenceImageIds', [])}
        session.add(Objective(id=run_id, project_id=project.id, revision=project.revision, status='pending',
            data={'objective': text, 'kind': 'project.capture', 'tasks': [], 'designRequest': request,
                  'modelPolicySnapshot': dict(project.data.get('modelPolicy', {})),
                  'toolPolicySnapshot': copy.deepcopy({'policy': project.data.get('state', {}).get('toolPolicy', {}),
                        'bindings': project.data.get('toolPolicyBindings', {})}),
                  'submittedBy': actor.user_id, 'createdAt': datetime.now(UTC).isoformat()}))
        project.data = {**project.data, 'state': state, 'status': 'queued', 'displayStage': 0}
        self.projects._emit(session, project, 'objective.submitted', {'runId': run_id, 'kind': 'project.capture'})
        return {'projectId': project.id, 'runId': run_id, 'revision': project.revision}

    def create(self, actor, brief, key, *, model_policy=None):
        if not isinstance(brief, str) or not brief.strip() or len(brief) > 12000:
            raise DomainError('Provide a bounded project brief', 'invalid_request', 422)
        def operation(session):
            project = Project(id=str(uuid4()), tenant_id=actor.tenant_id, owner_id=actor.user_id,
                revision=1, event_sequence=0, data={'brief': brief, 'name': brief[:80],
                    'createdAt': datetime.now(UTC).isoformat(), 'requirements': {}, 'interfaces': {},
                    'approvedDesign': None, 'decisions': [], 'modelPolicy': model_policy or {}})
            session.add(project)
            session.add(Membership(project_id=project.id, user_id=actor.user_id, role='owner'))
            self.projects._emit(session, project, 'project.created', {'projectId': project.id})
            return self._queue(session, project, actor, self._state(project), {})
        return self.projects._command(actor, key, {'operation': 'design.create', 'brief': brief,
                                                 'modelPolicy': model_policy or {}}, operation)

    def capture(self, actor, project_id, payload, expected_revision, key):
        text = payload.get('text')
        if text is not None and (not isinstance(text, str) or not text.strip() or len(text) > 12000):
            raise DomainError('Provide a bounded message', 'invalid_request', 422)
        client_id = payload.get('clientMessageId')
        if client_id is not None and (not isinstance(client_id, str) or not 1 <= len(client_id) <= 120):
            raise DomainError('Invalid message identity', 'invalid_request', 422)
        request_hash = fingerprint(payload)
        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            state = self._state(project)
            receipts = dict(state.get('messageRequests', {}))
            if client_id and client_id in receipts:
                prior = receipts[client_id]
                if prior['hash'] != request_hash:
                    raise DomainError('Message identity was reused for different input', 'idempotency_conflict')
                return prior['result']
            if project.revision != expected_revision:
                raise DomainError('Project changed; refresh before capture', 'revision_conflict')
            if len(state['conversation']) >= 500:
                raise DomainError('This conversation reached its bound; use a new scoped objective', 'conversation_limit')
            if text:
                state['conversation'].append({'id': client_id or str(uuid4()), 'role': 'user', 'text': text,
                    'referenceImageIds': payload.get('referenceImageIds', []), 'createdAt': datetime.now(UTC).isoformat()})
            result = self._queue(session, project, actor, state, payload)
            if client_id:
                receipts[client_id] = {'hash': request_hash, 'result': result}
                project.data = {**project.data, 'state': {**project.data['state'], 'messageRequests': receipts}}
            return result
        return self.projects._command(actor, key, {'operation': 'design.capture', 'projectId': project_id,
            'payload': payload, 'expectedRevision': expected_revision}, operation)

    def answer(self, actor, project_id, payload, expected_revision, key):
        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            if project.revision != expected_revision:
                raise DomainError('Question context changed', 'revision_conflict')
            state = self._state(project)
            clarification = state.get('clarification', {})
            question_id = payload.get('questionId', payload.get('questionKey'))
            found = next((item for item in clarification.get('questions', []) + clarification.get('resolvedQuestions', [])
                          if item['id'] == question_id), None)
            if found is None or (payload.get('questionHash') and payload['questionHash'] != found['questionHash']):
                raise DomainError('Question changed; refresh before answering', 'question_conflict')
            question = Question.model_validate({k: v for k, v in found.items() if k not in {'questionHash', 'resolved'}})
            values = payload.get('values', [])
            if (not isinstance(values, list) or len(values) > 12 or any(not isinstance(v, str) or len(v) > 1000 for v in values)
                    or (question.required and (not values or any(not v.strip() for v in values)))
                    or (question.type != 'multi_select' and len(values) > 1)):
                raise DomainError('Invalid answer values', 'invalid_request', 422)
            if question.type in {'single_select', 'multi_select'} and not question.allow_custom:
                if not set(values).issubset({option.value for option in question.options}):
                    raise DomainError('Answer is not an offered choice', 'invalid_request', 422)
            if question.type == 'number' and values:
                try:
                    value = float(values[0])
                except ValueError:
                    raise DomainError('Provide a number', 'invalid_request', 422) from None
                if not math.isfinite(value) or (question.min is not None and value < question.min) or (question.max is not None and value > question.max):
                    raise DomainError('Answer is outside the question range', 'invalid_request', 422)
            clarification['answers'] = {**clarification.get('answers', {}), question_id: values}
            clarification['resolvedQuestions'] = [q for q in clarification.get('resolvedQuestions', []) if q['id'] != question_id] + [found]
            clarification['questions'] = [q for q in clarification.get('questions', []) if q['id'] != question_id]
            state['conversation'].append({'id': str(uuid4()), 'role': 'user',
                                          'text': question.prompt + ': ' + ', '.join(values)})
            state['clarification'] = clarification
            if payload.get('autoResume') is False:
                state['clarification'].update(needsReview=True, readyToConfirm=False)
                project.advance_revision()
                project.data = {**project.data, 'state': state}
                self.projects._emit(session, project, 'capture.answer.saved', {'questionId': question_id})
                return {'projectId': project.id, 'revision': project.revision}
            return self._queue(session, project, actor, state, {'text': 'Apply the saved answers and update the concept.',
                'selectedPartId': state.get('redoPartId'), 'conceptVersion': state.get('concept', {}).get('version')})
        return self.projects._command(actor, key, {'operation': 'design.answer', 'projectId': project_id,
            'payload': payload, 'expectedRevision': expected_revision}, operation)

    def request(self, scope):
        with self.projects.transaction() as session:
            _, run = GraphRepository(self.projects)._check(session, scope)
            if run.data.get('kind') != 'project.capture' or 'designRequest' not in run.data:
                raise DomainError('Objective is not a capture request', 'run_conflict')
            return copy.deepcopy(run.data['designRequest'])

    def publish(self, scope, result):
        records = [observation for observation in result.observations if observation.name == 'design.capture']
        if result.status != 'succeeded' or len(records) != 1:
            raise DomainError('Capture did not return a typed candidate', 'invalid_result')
        candidate = CaptureResult.model_validate(records[0].details['candidate'])
        digest = fingerprint(candidate.model_dump(mode='json'))
        with self.projects.transaction() as session:
            project, run = GraphRepository(self.projects)._check(session, scope)
            if run.data.get('designPublicationHash'):
                if run.data['designPublicationHash'] != digest:
                    raise DomainError('Capture publication changed during replay', 'result_conflict')
                return
            request, state = run.data['designRequest'], self._state(project)
            selected = request.get('selectedPartId')
            scene = candidate.scene
            requirements = candidate.requirements
            constraints = [item.model_dump(mode='json', by_alias=True) for item in candidate.constraints]
            if selected:
                baseline = ConceptScene.model_validate(request['baselineConcept']['scene'])
                if state.get('concept', {}).get('version') != request['conceptVersion']:
                    raise DomainError('Concept version changed during part redo', 'concept_conflict')
                if scene:
                    if len(scene.parts) != 1 or scene.parts[0].id != selected:
                        raise DomainError('Part redo must return exactly the selected stable part ID', 'part_scope_conflict')
                    scene = baseline.model_copy(update={'parts': [scene.parts[0] if p.id == selected else p for p in baseline.parts]})
                    scene = ConceptScene.model_validate(scene.model_dump())
                old = request.get('baselineProposal', {})
                if requirements and requirements != old.get('requirements', {}).get('items', []):
                    raise DomainError('Part redo cannot rewrite unrelated requirements', 'part_scope_conflict')
                requirements = old.get('requirements', {}).get('items', [])
                kept = [c for c in old.get('constraints', []) if selected not in c.get('partIds', [])]
                if any(selected not in c['partIds'] or c['id'] in {k['id'] for k in kept} for c in constraints):
                    raise DomainError('Part redo cannot rewrite unrelated constraints', 'part_scope_conflict')
                constraints = kept + constraints
            validate_user_requirements(project, requirements, scene_present=scene is not None)
            old_scene = state.get('concept', {}).get('scene')
            part_ids = {part.id for part in scene.parts} if scene else {p['id'] for p in (old_scene or {}).get('parts', [])}
            sources = {'brief', *[message['id'] for message in state['conversation'] if message['role'] == 'user'],
                       *project.data.get('referenceImageIds', [])}
            if any(not set(c['partIds']).issubset(part_ids) or not set(c['sourceIds']).issubset(sources)
                   or (c['certainty'] == 'known' and not c['sourceIds']) for c in constraints):
                raise DomainError('Design constraints refer to unknown parts or evidence', 'invalid_result')
            if scene:
                previous = state.get('concept', {})
                state['concept'] = {'version': previous.get('version', 0) + 1,
                    'scene': scene.model_dump(mode='json', by_alias=True), 'stale': False, 'sourceRunId': run.id}
                state['redoPartId'] = None
            elif state.get('concept'):
                state['concept']['stale'] = True
            questions = []
            for question in candidate.questions:
                data = question.model_dump(mode='json', by_alias=True)
                questions.append({**data, 'questionHash': fingerprint(data)})
            existing = state.get('clarification', {})
            answered = {q['id']: q for q in existing.get('resolvedQuestions', [])}
            answers = dict(existing.get('answers', {}))
            for question in questions:
                if question['id'] in answered and answered[question['id']]['questionHash'] != question['questionHash']:
                    answered.pop(question['id'])
                    answers.pop(question['id'], None)
            outstanding = [q for q in questions if q['id'] not in answered or answered[q['id']]['questionHash'] != q['questionHash']]
            ready = (candidate.ready_to_review and bool(scene) and not any(q['required'] for q in outstanding)
                     and not any(c['critical'] and c['certainty'] == 'unknown' for c in constraints))
            state['proposal'] = {'summary': candidate.message, 'requirements': {'items': requirements},
                'constraints': constraints, 'limitations': [*candidate.limitations, 'Concept illustration; engineering validation is separate.'],
                'clarificationQuestions': outstanding, 'sources': [], 'referenceSourceIds': sorted(sources)}
            state['clarification'] = {'questions': outstanding, 'resolvedQuestions': list(answered.values()),
                'answers': answers, 'needsReview': False, 'readyToConfirm': ready}
            state['planning'] = {'phase': 'capture', 'pending': False, 'runId': run.id}
            state['conversation'].append({'id': 'capture-' + run.id, 'role': 'assistant', 'text': candidate.message,
                                           'createdAt': datetime.now(UTC).isoformat()})
            state['confirmed'] = False
            if project.data.get('approvedDesign'):
                state['approvedDesignStatus'] = 'superseded'
            project.data = {**project.data, 'state': state, 'status': 'review', 'displayStage': 0}
            run.data = {**run.data, 'designPublicationHash': digest, 'designCandidate': candidate.model_dump(mode='json')}
            self.projects._emit(session, project, 'concept.updated', {'runId': run.id,
                'conceptVersion': state.get('concept', {}).get('version'), 'readyToConfirm': ready})

    def confirm(self, actor, project_id, expected_revision, key):
        if execution_owner.get() is not None:
            raise DomainError('Only a user command can confirm a concept', 'approval_required')
        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            if project.revision != expected_revision:
                raise DomainError('Concept changed before confirmation', 'revision_conflict')
            state = self._state(project)
            if state.get('confirmed'):
                raise DomainError('This concept is already confirmed', 'concept_confirmed')
            if state.get('planning', {}).get('pending') or not state.get('clarification', {}).get('readyToConfirm'):
                raise DomainError('Resolve or review the current concept before confirming', 'concept_not_ready')
            validate_user_requirements(project, state.get('proposal', {}).get('requirements', {}).get('items', []))
            active = [run for run in session.scalars(select(Objective).where(Objective.project_id == project.id,
                Objective.revision == project.context_revision, Objective.status.in_(['pending', 'running', 'waiting'])))
                if run.data.get('kind') != 'project.capture']
            if active:
                raise DomainError('Finish or cancel the current objective before confirming a new concept', 'run_conflict')
            scene = ConceptScene.model_validate(state['concept']['scene'])
            scene_data = scene.model_dump(mode='json', by_alias=True)
            scene_hash = fingerprint(scene_data)
            prior_designs = [*project.data.get('approvedDesignHistory', []), project.data.get('approvedDesign') or {}]
            approved = {'schemaVersion': 'approved-design-v1', 'version': max(
                        (item.get('version', 0) for item in prior_designs), default=0) + 1, 'sceneVersion': state['concept']['version'],
                'sceneHash': scene_hash, 'scene': scene_data, 'approvedBy': actor.user_id,
                'approvedAt': datetime.now(UTC).isoformat(), 'units': 'relative-illustration',
                'explodeAffectsManufacturing': False,
                'peripheralConceptIds': sorted(p.id for p in scene.parts if p.circuit_role == 'peripheral')}
            history = list(project.data.get('approvedDesignHistory', []))
            if project.data.get('approvedDesign'):
                history.append(project.data['approvedDesign'])
            state.update(confirmed=True, approvedDesign=approved, approvedDesignStatus='current',
                designIntentCheck={'status': 'UNKNOWN', 'details': {'reasonCode': 'awaiting_engineering_checks'}})
            project.advance_revision()
            run_id = str(uuid4())
            run = Objective(id=run_id, project_id=project.id, revision=project.revision, status='pending',
                data={'objective': 'Complete the engineering requested by this confirmed brief using its approved requirements and design: '
                      + project.data['brief'], 'kind': 'objective', 'tasks': [], 'submittedBy': actor.user_id,
                      'modelPolicySnapshot': dict(project.data.get('modelPolicy', {})),
                      'toolPolicySnapshot': copy.deepcopy({'policy': project.data.get('state', {}).get('toolPolicy', {}),
                        'bindings': project.data.get('toolPolicyBindings', {})}),
                      'createdAt': datetime.now(UTC).isoformat()})
            session.add(run)
            design_intent = {'sceneHash': scene_hash, 'partIds': [p.id for p in scene.parts],
                'units': 'relative-illustration', 'explodeAffectsManufacturing': False}
            interfaces = {**project.data.get('interfaces', {}), 'designIntent': {
                **design_intent, 'referenceOnly': True, 'engineeringValidated': False}}
            if approved['peripheralConceptIds']:
                # A circuit for an older approved concept cannot silently authorize this one.
                interfaces.pop('circuit', None)
                state['interfacesAgreed'] = False
                state['planning'] = {'phase': 'interfaces', 'pending': True, 'runId': run_id}
                if state.get('interfaceProposal'):
                    state['interfaceProposal']['status'] = 'superseded'
            project.data = {**project.data, 'approvedDesign': approved, 'approvedDesignHistory': history,
                'requirements': {**state['proposal']['requirements'],
                    **({'userEdits': copy.deepcopy(project.data['requirements']['userEdits'])}
                       if 'userEdits' in project.data.get('requirements', {}) else {}),
                    'constraints': state['proposal']['constraints'],
                    'designIntent': design_intent}, 'interfaces': interfaces,
                'state': state, 'status': 'queued', 'displayStage': 2}
            if approved['peripheralConceptIds']:
                from kleos.projects.interfaces import baseline
                run.data = {**run.data, 'kind': 'project.solveInterfaces',
                    'objective': 'Propose the circuit interface needed for the approved electronic peripheral concept IDs. '
                        'Include firmware only if requested by the project. All choices require user review: ' + project.data['brief'],
                    'interfaceRequest': {'baselineHash': baseline(project), 'previousCandidate': None}}
            self.projects._emit(session, project, 'concept.confirmed', {'sceneHash': scene_hash, 'runId': run_id})
            return {'projectId': project.id, 'runId': run_id, 'revision': project.revision}
        return self.projects._command(actor, key, {'operation': 'design.confirm', 'projectId': project_id,
                                                 'expectedRevision': expected_revision}, operation)
