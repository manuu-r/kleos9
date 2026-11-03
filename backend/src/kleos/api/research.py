"""Existing feasibility action, with an explicit optional source-only scope."""
from typing import Literal

from pydantic import Field

from kleos.contracts.models import DomainError, WireModel

COMMANDS = {'project.research'}


class ResearchRequest(WireModel):
    project_id: str
    objective: str | None = Field(default=None, min_length=1, max_length=6000)
    scope: Literal['web', 'project_sources'] = 'web'


class ResearchFacade:
    def __init__(self, service):
        self.service = service

    def command(self, actor, body, key):
        if body['type'] not in COMMANDS:
            raise DomainError('Unsupported research command', 'capability_unavailable', 501)
        request = ResearchRequest.model_validate(body.get('payload', {}))
        return self.service.submit(actor, request.project_id, body.get('expectedRevision'), key,
            objective=request.objective, scope=request.scope)
