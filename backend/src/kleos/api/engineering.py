"""Existing workbench command names; no fixed stage workflow or synthetic success."""
from pydantic import Field

from kleos.contracts.models import DomainError, WireModel

COMMANDS = {'project.generate', 'project.repair', 'project.cancel', 'project.retry',
            'project.verify', 'project.retryNative'}


class EngineeringRequest(WireModel):
    project_id: str
    objective: str | None = Field(default=None, min_length=1, max_length=6000)
    allowed_paths: list[str] = Field(default_factory=list, max_length=128)


class RunRequest(WireModel):
    project_id: str
    run_id: str | None = None


class NativeRebuildRequest(WireModel):
    project_id: str
    job_id: str = Field(min_length=1, max_length=128)


class EngineeringFacade:
    def __init__(self, service, rebuild=None, verification=None):
        self.service = service
        self.rebuild = rebuild
        self.verification = verification

    def command(self, actor, body, key):
        kind, payload, revision = body['type'], body.get('payload', {}), body.get('expectedRevision')
        if kind in {'project.generate', 'project.repair'}:
            request = EngineeringRequest.model_validate(payload)
            return self.service.submit(actor, request.project_id, kind, revision, key,
                                       objective=request.objective, allowed_paths=request.allowed_paths)
        if kind in {'project.cancel', 'project.retry'}:
            request = RunRequest.model_validate(payload)
            if (kind == 'project.cancel' and self.verification is not None
                    and self.verification.has_active(actor, request.project_id, run_id=request.run_id)):
                return self.verification.cancel_current(actor, request.project_id, revision, key,
                    run_id=request.run_id)
            operation = self.service.cancel if kind == 'project.cancel' else self.service.retry
            return operation(actor, request.project_id, request.run_id, revision, key)
        if kind == 'project.verify':
            if self.verification is None:
                raise DomainError('Independent checking is not configured', 'verification_unavailable', 503)
            request = RunRequest.model_validate(payload)
            return self.verification.submit(actor, request.project_id, revision, key,
                policy_key=None, run_id=request.run_id)
        if kind == 'project.retryNative':
            if self.rebuild is None:
                raise DomainError('Exact-source native rebuilding is not configured', 'native_retry_unavailable', 503)
            request = NativeRebuildRequest.model_validate(payload)
            return self.rebuild.submit(actor, request.project_id, request.job_id, revision, key)
        raise DomainError('Unsupported engineering command', 'capability_unavailable', 501)
