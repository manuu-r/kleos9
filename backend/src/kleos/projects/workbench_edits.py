"""Explicit user requirement edits; recapture never grants compatibility or release approval."""
import copy
import json
import math
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from kleos.contracts.models import DomainError
from kleos.projects.graph_repository import execution_owner
from kleos.projects.store import Objective, fingerprint


def battery_requirement(battery):
    dimensions = battery['dimensionsMm']
    def number(value):
        value = float(value)
        return str(int(value)) if value.is_integer() else repr(value)
    return (f"Battery: capacity {number(battery['capacityMah'])} mAh; dimensions "
        f"x={number(dimensions['x'])}, y={number(dimensions['y'])}, z={number(dimensions['z'])} mm "
        '(user specified; compatibility unverified).')


def user_requirement_snapshot(project):
    requirements = project.requirements if hasattr(project, 'requirements') else project.data.get('requirements', {})
    values = copy.deepcopy(requirements.get('userEdits', {}))
    features = values.get('features', {})
    required = sorted(name for name, included in features.items() if included)
    if values.get('battery'):
        required.append(battery_requirement(values['battery']))
    return {'values': values, 'requiredRequirements': required,
            'excludedRequirements': sorted(name for name, included in features.items() if not included)}


def validate_user_requirements(project, requirements, *, scene_present=True):
    """Exact structured-intent consistency, not semantic or engineering validation."""
    if not scene_present:
        return  # A question-only draft remains unconfirmed and may ask about feasibility.
    edits = user_requirement_snapshot(project)
    if (not set(edits['requiredRequirements']).issubset(requirements)
            or set(edits['excludedRequirements']).intersection(requirements)):
        raise DomainError('Concept requirements differ from the explicit user edits; revise the candidate',
                          'requirement_override_conflict')


class WorkbenchEdits:
    def __init__(self, projects, design):
        self.projects, self.design = projects, design

    @staticmethod
    def _number(value, bound):
        if type(value) not in {int, float} or not 0 < value <= bound or not math.isfinite(value):
            raise DomainError('Battery values must be finite, positive and within the input bounds', 'invalid_request', 422)
        return float(value)

    def _payload(self, kind, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get('projectId'), str) or not 1 <= len(payload['projectId']) <= 36:
            raise DomainError('Choose a project for the requirement edit', 'invalid_request', 422)
        if kind == 'project.toggleFeature':
            name = payload.get('name')
            if (set(payload) != {'projectId', 'name'} or not isinstance(name, str)
                    or not name.strip() or name != name.strip() or len(name) > 1000):
                raise DomainError('Choose the exact current requirement name', 'invalid_request', 422)
            return {'name': name}
        if kind != 'project.swapBattery' or set(payload) != {'projectId', 'capacityMah', 'dimensionsMm'}:
            raise DomainError('Provide explicit battery capacity and dimensions', 'invalid_request', 422)
        dimensions = payload['dimensionsMm']
        if not isinstance(dimensions, dict) or set(dimensions) != {'x', 'y', 'z'}:
            raise DomainError('Battery dimensions require x, y and z in millimetres', 'invalid_request', 422)
        return {'battery': {'capacityMah': self._number(payload['capacityMah'], 1_000_000),
            'dimensionsMm': {axis: self._number(dimensions[axis], 10000) for axis in ('x', 'y', 'z')},
            'source': 'user', 'compatibilityStatus': 'UNKNOWN'}}

    @staticmethod
    def _invalidate(project, state, actor, kind, edit_id):
        old = project.data
        history = copy.deepcopy(old.get('workbenchEditHistory', []))
        entry = {'id': edit_id, 'type': kind, 'editedBy': actor.user_id,
            'createdAt': datetime.now(UTC).isoformat(), 'contextRevision': project.context_revision,
            'requirements': copy.deepcopy(old.get('requirements', {})),
            'interfaces': copy.deepcopy(old.get('interfaces', {})),
            'approvedDesign': copy.deepcopy(old.get('approvedDesign')),
            'acceptedInterfaces': copy.deepcopy(old.get('acceptedInterfaces')),
            'acceptedCircuitIdentity': copy.deepcopy(old.get('acceptedCircuitIdentity')),
            'acceptedReleaseId': old.get('acceptedReleaseId'),
            'sourceManifestHash': fingerprint(old.get('sourceManifest', {}))}
        if len(history) >= 256 or len(json.dumps([*history, entry], allow_nan=False).encode()) > 2 * 1024**2:
            raise DomainError('Requirement edit history reached its bound', 'history_limit')
        history.append(entry)
        designs = copy.deepcopy(old.get('approvedDesignHistory', []))
        if old.get('approvedDesign') and old['approvedDesign'] not in designs:
            if len(designs) >= 256:
                raise DomainError('Approved design history reached its bound', 'history_limit')
            designs.append(copy.deepcopy(old['approvedDesign']))
        releases = list(old.get('supersededReleaseIds', []))
        if old.get('acceptedReleaseId') and old['acceptedReleaseId'] not in releases:
            if len(releases) >= 256:
                raise DomainError('Release history reached its bound', 'history_limit')
            releases.append(old['acceptedReleaseId'])
        state.update(confirmed=False, interfacesAgreed=False, approvedDesign=None,
            approvedDesignStatus='superseded', previousAcceptedReleaseId=old.get('acceptedReleaseId'),
            designIntentCheck={'status': 'UNKNOWN', 'details': {'reasonCode': 'user_requirements_changed'}})
        if state.get('concept'):
            state['concept']['stale'] = True
        if state.get('interfaceProposal'):
            state['interfaceProposal']['status'] = 'superseded'
        project.data = {**old, 'workbenchEditHistory': history, 'approvedDesignHistory': designs,
            'supersededReleaseIds': releases, 'approvedDesign': None, 'interfaces': {},
            'acceptedInterfaces': None, 'acceptedCircuitIdentity': None, 'acceptedReleaseId': None}

    def command(self, actor, body, key):
        if execution_owner.get() is not None:
            raise DomainError('Only a user command can revise project requirements', 'approval_required')
        kind, payload = body.get('type'), body.get('payload')
        parsed = self._payload(kind, payload)

        def operation(session):
            project = self.projects.authorized(session, actor, payload['projectId'], write=True)
            if type(body.get('expectedRevision')) is not int or project.revision != body['expectedRevision']:
                raise DomainError('Project changed; refresh before revising requirements', 'revision_conflict')
            state = self.design._state(project)
            if len(state['conversation']) >= 500:
                raise DomainError('Conversation reached its bound', 'conversation_limit')
            requirements = copy.deepcopy(project.data.get('requirements', {}))
            edits = copy.deepcopy(requirements.get('userEdits', {}))
            if 'name' in parsed:
                name = parsed['name']
                items = state.get('proposal', {}).get('requirements', {}).get('items', [])
                known = edits.get('features', {})
                # Previously removed names remain restorable; they were admitted from
                # an exact candidate, never invented by a later client payload.
                if (not isinstance(items, list) or items.count(name) > 1
                        or (items.count(name) != 1 and not (name in known and known[name] is False))):
                    raise DomainError('Requirement name is missing or ambiguous in the current candidate', 'feature_not_found', 404)
                features = {**known, name: not known.get(name, True)}
                if len(features) > 50:
                    raise DomainError('Feature edit allowance reached its bound', 'history_limit')
                edits['features'] = features
                text = ('Include' if features[name] else 'Exclude') + ' this exact requirement: ' + json.dumps(name)
                state['features'] = [{'name': item, 'status': 'requested' if features.get(item, True) else 'dropped'}
                    for item in dict.fromkeys([*items, *features])]
            else:
                prior = edits.get('battery')
                if prior:
                    requirements['items'] = [item for item in requirements.get('items', []) if item != battery_requirement(prior)]
                edits['battery'] = parsed['battery']
                text = 'Revise the battery requirement: ' + battery_requirement(parsed['battery'])
            edit_id = str(uuid4())
            self._invalidate(project, state, actor, kind, edit_id)
            requirements['userEdits'] = edits
            project.data = {**project.data, 'requirements': requirements}
            snapshot = user_requirement_snapshot(project)
            retained = [item for item in requirements.get('items', []) if item not in snapshot['excludedRequirements']]
            requirements['items'] = list(dict.fromkeys([*retained, *snapshot['requiredRequirements']]))
            project.data = {**project.data, 'requirements': requirements}
            state['conversation'].append({'id': edit_id, 'role': 'user', 'text': text,
                'createdAt': datetime.now(UTC).isoformat()})
            for run in session.scalars(select(Objective).where(Objective.project_id == project.id,
                    Objective.status.in_(['pending', 'running', 'waiting']))):
                run.status = 'cancelled'
                self.projects._emit(session, project, 'objective.cancelled',
                    {'runId': run.id, 'reason': 'user_requirements_changed'})
            instruction = (text + '\nUpdate the candidate concept and identify compatibility questions. '
                'Preserve unrelated intent and sources. Keep exact user requirement strings supplied in userEdits; '
                'do not infer chemistry, voltage, charging safety, a supplier part, geometry fit or approval. '
                'Any engineering compatibility remains UNKNOWN until separately checked.')
            receipt = self.design._queue(session, project, actor, state, {'text': instruction})
            self.projects._emit(session, project, 'project.requirementsEdited',
                {'editId': edit_id, 'type': kind, 'userEdits': edits, 'compatibilityStatus': 'UNKNOWN',
                 'runId': receipt['runId']})
            return receipt

        return self.projects._command(actor, key, {'type': kind, 'payload': payload,
            'expectedRevision': body.get('expectedRevision')}, operation)
