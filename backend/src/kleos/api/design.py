"""Existing workbench capture commands mapped to typed candidate design work."""
from pydantic import Field

from kleos.contracts.models import DomainError, WireModel

COMMANDS = {'project.create', 'project.capture', 'project.requestReview',
            'project.message', 'project.answer', 'project.confirm'}


class CreateRequest(WireModel):
    brief: str = Field(min_length=1, max_length=12000)
    auto_start: bool = True
    simulation: bool = False
    model_policy: dict | None = None


class CaptureRequest(WireModel):
    project_id: str
    text: str | None = Field(default=None, max_length=12000)
    client_message_id: str | None = Field(default=None, max_length=120)
    selected_part_id: str | None = None
    concept_version: int | None = Field(default=None, ge=1)
    reference_image_ids: list[str] = Field(default_factory=list, max_length=8)
    auto_resume: bool = True


class AnswerRequest(WireModel):
    project_id: str
    question_id: str | None = None
    question_key: str | None = None
    question_hash: str | None = None
    values: list[str] = Field(max_length=12)
    auto_resume: bool = True


class ConfirmRequest(WireModel):
    project_id: str


class DesignFacade:
    def __init__(self, service, policies=None):
        self.service = service
        self.policies = policies

    def handles(self, actor, body):
        kind, payload = body['type'], body.get('payload', {})
        if kind not in COMMANDS:
            return False
        if kind == 'project.create':
            return payload.get('autoStart') is True and not payload.get('simulation')
        if kind == 'project.message':
            if payload.get('runId') or 'text' not in payload:
                return False
            with self.service.projects.transaction() as session:
                project = self.service.projects.authorized(session, actor, payload.get('projectId'), write=True)
                return bool(payload.get('selectedPartId')) or (
                    not project.data.get('sourceManifest') and not project.data.get('state', {}).get('confirmed', False))
        return True

    def command(self, actor, body, idempotency_key):
        kind, payload, revision = body['type'], body.get('payload', {}), body.get('expectedRevision')
        if kind == 'project.create':
            request = CreateRequest.model_validate(payload)
            if request.model_policy is not None and self.policies is None:
                raise DomainError('Model policy validation is not configured', 'model_unavailable', 503)
            policy = self.policies.validate(request.model_policy) if request.model_policy is not None else None
            return self.service.create(actor, request.brief, idempotency_key, model_policy=policy)
        if kind == 'project.confirm':
            request = ConfirmRequest.model_validate(payload)
            return self.service.confirm(actor, request.project_id, revision, idempotency_key)
        if kind == 'project.answer':
            request = AnswerRequest.model_validate(payload)
            return self.service.answer(actor, request.project_id,
                request.model_dump(mode='json', by_alias=True, exclude={'project_id'}, exclude_none=True), revision, idempotency_key)
        request = CaptureRequest.model_validate(payload)
        return self.service.capture(actor, request.project_id,
            request.model_dump(mode='json', by_alias=True, exclude={'project_id'}, exclude_none=True), revision, idempotency_key)
