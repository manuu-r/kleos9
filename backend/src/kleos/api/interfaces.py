"""Unchanged solve/message commands plus explicit exact-proposal approval."""
from typing import Literal

from pydantic import Field

from kleos.contracts.models import DomainError, WireModel

COMMANDS = {'project.solveInterfaces', 'project.approveInterfaces'}


class SolveRequest(WireModel):
    project_id: str
    objective: str | None = Field(default=None, min_length=1, max_length=6000)


class ApproveRequest(WireModel):
    project_id: str
    proposal_id: str = Field(min_length=1, max_length=100)
    proposal_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class MessageRequest(WireModel):
    project_id: str
    text: str = Field(min_length=1, max_length=6000)
    client_message_id: str | None = Field(default=None, max_length=120)
    auto_resume: Literal[True] = True
    reference_image_ids: list[str] = Field(default_factory=list, max_length=8)
    selected_part_id: str | None = Field(default=None, max_length=100)
    concept_version: int | None = Field(default=None, ge=1)


def approval_text(text):
    return isinstance(text, str) and text.strip().casefold() == 'approve interfaces'


class InterfaceFacade:
    def __init__(self, service):
        self.service = service

    def handles(self, actor, body):
        if body['type'] in COMMANDS:
            return True
        payload = body.get('payload', {})
        if body['type'] != 'project.message' or payload.get('runId'):
            return False
        if approval_text(payload.get('text')):
            return True  # Missing/stale proposals fail explicitly; never become generic instructions.
        if payload.get('selectedPartId'):
            return False
        with self.service.projects.transaction() as session:
            project = self.service.projects.authorized(session, actor, payload.get('projectId'), write=True)
            return project.data.get('state', {}).get('interfaceProposal', {}).get('status') in {'pending', 'needs_input'}

    def command(self, actor, body, idempotency_key):
        kind, payload, revision = body['type'], body.get('payload', {}), body.get('expectedRevision')
        if kind == 'project.approveInterfaces':
            request = ApproveRequest.model_validate(payload)
            return self.service.approve(actor, request.project_id, revision, idempotency_key,
                proposal_id=request.proposal_id, proposal_hash=request.proposal_hash)
        if kind == 'project.message':
            request = MessageRequest.model_validate(payload)
            if approval_text(request.text):
                if request.reference_image_ids:
                    raise DomainError('Review new image context in a new proposal before approving interfaces', 'proposal_conflict')
                return self.service.approve(actor, request.project_id, revision, idempotency_key, message=True)
            return self.service.solve(actor, request.project_id, revision, idempotency_key, objective=request.text,
                reference_image_ids=request.reference_image_ids)
        request = SolveRequest.model_validate(payload)
        return self.service.solve(actor, request.project_id, revision, idempotency_key, objective=request.objective)
