"""Explicit project training preference; never dispatches training or exports data."""
from kleos.contracts.models import DomainError


def set_training_consent(projects, actor, body, key):
    payload = body.get('payload', {})
    opted_in = payload.get('optedIn')
    if type(opted_in) is not bool or set(payload) - {'projectId', 'optedIn'}:
        raise DomainError('Provide an explicit training preference', 'invalid_request', 422)
    def operation(session):
        project = projects.authorized(session, actor, payload.get('projectId'), write=True)
        if project.revision != body.get('expectedRevision'):
            raise DomainError('Project changed; refresh before changing consent', 'revision_conflict')
        project.advance_revision(context=False)
        project.data = {**project.data, 'trainingConsent': {'optedIn': opted_in,
            'recordedBy': actor.user_id, 'revision': project.revision}}
        projects._emit(session, project, 'project.trainingConsentChanged',
            {'optedIn': opted_in, 'trainingStarted': False})
        return {'projectId': project.id, 'revision': project.revision}
    return projects._command(actor, key, {'type': 'project.setTrainingConsent',
        'payload': payload, 'expectedRevision': body.get('expectedRevision')}, operation)
