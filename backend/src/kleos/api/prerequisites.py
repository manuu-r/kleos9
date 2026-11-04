"""Existing chat messages may answer an exact durable interface prerequisite."""
from kleos.contracts.models import DomainError


class PrerequisiteFacade:
    """Routing only: the service owns proposal, interrupt, authority and replay checks."""
    def __init__(self, service):
        self.service = service

    def dispatch(self, actor, body, idempotency_key):
        if body.get('type') != 'project.message':
            return None
        payload = body.get('payload', {})
        if isinstance(payload, dict) and payload.get('runId'):
            # Explicit generic answers retain their existing route. That route
            # must refuse prerequisite interrupts; it cannot approve interfaces.
            return None
        if (not isinstance(payload, dict) or not isinstance(payload.get('projectId'), str)
                or not 1 <= len(payload['projectId']) <= 36):
            raise DomainError('Provide a valid project identity', 'invalid_request', 422)
        # The full original envelope, including revision and optional context,
        # participates in the service's immutable command receipt. The service
        # validates bounded message fields and refuses mixed new context only
        # after recognizing an actual prerequisite or its own receipt replay;
        # ordinary messages and explicit interface flows return None unchanged.
        result = self.service.dispatch_message(actor, body, idempotency_key)
        if result is None:
            return None
        # Private receipt routing metadata stays in durable storage, outside the
        # unchanged CommandAccepted wire contract.
        return {key: value for key, value in result.items() if key != 'prerequisiteRequestId'}
