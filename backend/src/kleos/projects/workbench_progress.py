"""User workbench preferences and simulated navigation, without physical authority."""
from datetime import UTC, datetime

from kleos.contracts.models import DomainError
from kleos.projects.graph_repository import execution_owner

COMMANDS = {'project.selectPrint', 'project.assemblyStep'}
MAX_ASSEMBLY_STEP = 255  # Defensive counter bound, never an invented guide length.


class WorkbenchProgress:
    def __init__(self, projects, *, clock=lambda: datetime.now(UTC)):
        self.projects, self.clock = projects, clock

    @staticmethod
    def _payload(body):
        if not isinstance(body, dict) or set(body)-{'type', 'payload', 'expectedRevision'}:
            raise DomainError('Invalid workbench progress command', 'invalid_request', 422)
        kind, payload = body.get('type'), body.get('payload')
        if kind not in COMMANDS:
            raise DomainError('Unsupported workbench progress command', 'capability_unavailable', 501)
        field = 'mode' if kind == 'project.selectPrint' else 'step'
        if (not isinstance(payload, dict) or set(payload) != {'projectId', field}
                or not isinstance(payload.get('projectId'), str)
                or not 1 <= len(payload['projectId']) <= 36):
            raise DomainError('Provide a project and explicit workbench choice', 'invalid_request', 422)
        if type(body.get('expectedRevision')) is not int or body['expectedRevision'] < 1:
            raise DomainError('Provide the current project revision', 'invalid_request', 422)
        value = payload[field]
        if kind == 'project.selectPrint':
            if not isinstance(value, str) or value not in {'home', 'order'}:
                raise DomainError('Printing preference must be home or order', 'invalid_request', 422)
        elif type(value) is not int or not 0 <= value <= MAX_ASSEMBLY_STEP:
            raise DomainError('Assembly progress must be a bounded integer step', 'invalid_request', 422)
        return kind, payload, value

    def command(self, actor, body, key):
        if execution_owner.get() is not None:
            raise DomainError('Only a user command may record workbench progress', 'approval_required')
        kind, payload, value = self._payload(body)

        def operation(session):
            project = self.projects.authorized(session, actor, payload['projectId'], write=True)
            if project.revision != body['expectedRevision']:
                raise DomainError('Project changed; refresh before recording progress', 'revision_conflict')
            state = dict(project.data.get('state', {}))
            if kind == 'project.assemblyStep':
                previous = state.get('assemblyStep', -1)
                if type(previous) is not int or not -1 <= previous <= MAX_ASSEMBLY_STEP:
                    raise DomainError('Stored assembly navigation is invalid', 'progress_conflict')
                if value != previous and abs(value-previous) != 1:
                    raise DomainError('Record the first step or an adjacent assembly step', 'progress_conflict')
            project.advance_revision(context=False)
            record = {'recordedBy': actor.user_id, 'recordedAt': self.clock().isoformat(),
                      'revision': project.revision, 'contextRevision': project.context_revision,
                      'physicalAcceptance': False}
            if kind == 'project.selectPrint':
                record.update(mode=value, status='recorded', evidenceKind='user_preference',
                              printingStarted=False, ordersPlaced=False)
                state.update(printMode=value, printPreference=record)
                event = 'project.printPreferenceRecorded'
            else:
                record.update(step=value, status='simulated', evidenceKind='synthetic',
                              assemblyCompletionStatus='UNKNOWN', activationStarted=False)
                state.update(assemblyStep=value, assemblyProgress=record)
                event = 'project.assemblyProgressRecorded'
            project.data = {**project.data, 'state': state}
            self.projects._emit(session, project, event, record)
            return {'projectId': project.id, 'revision': project.revision}

        return self.projects._command(actor, key, body, operation,
            replay_authorizer=lambda session, result: self.projects.authorized(
                session, actor, result['projectId'], write=True))
