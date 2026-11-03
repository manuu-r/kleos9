"""Private operational model records, without prompts or provider reasoning."""
from kleos.contracts.models import DomainError
from kleos.orchestration.models import ModelAttempt
from kleos.projects.store import Objective


def model_record(projects, access, actor, invocation_id):
    with projects.transaction() as session:
        attempt = session.get(ModelAttempt, invocation_id)
        run = session.get(Objective, attempt.run_id) if attempt else None
        if run is None:
            raise DomainError('Model record not found', 'not_found', 404)
        projects.authorized(session, actor, run.project_id)
        access._member(session, actor, admin=True)
        data = attempt.data
        model = data['model']
        usage = data.get('usage') or {}
        return {
            'id': attempt.id, 'projectId': run.project_id, 'runId': run.id,
            'taskId': data.get('taskId'), 'status': attempt.status,
            'scope': 'Operational metadata only; prompts, response content and private reasoning are not retained.',
            'model': {key: model[key] for key in ('key', 'provider', 'model', 'version')},
            'requestHash': data['requestHash'], 'fallbackOf': data.get('fallbackOf'),
            'createdAt': data.get('createdAt'), 'finishedAt': data.get('finishedAt'),
            'usage': {key: usage[key] for key in ('input_tokens', 'output_tokens', 'total_tokens')
                      if type(usage.get(key)) is int},
            'reservedMicroUsd': data['reservedMicroUsd'],
            'committedMicroUsd': data['committedMicroUsd'],
            'usageKnown': data.get('usage') is not None or attempt.status in {'rate_limited', 'provider_rejected'},
        }
