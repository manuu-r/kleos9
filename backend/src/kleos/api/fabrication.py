"""Frozen workbench print-preparation shapes over isolated digital preparation."""
from uuid import uuid4

from pydantic import Field

from kleos.contracts.models import DomainError, WireModel

QUERIES = {'print_preparations', 'print_preparation', 'print_dispatches', 'print_dispatch'}
ACTIONS = {'prepare_print', 'delete_print_preparation', 'complete_print_preparation',
           'create_print_dispatch', 'print_send', 'print_cancel', 'print_reconcile'}


class PrepareRequest(WireModel):
    release_id: str | None = Field(default=None, min_length=1, max_length=36)
    artifact_id: str = Field(min_length=1, max_length=36)
    profile_id: str = Field(min_length=1, max_length=100)
    expected_engineering_revision: int = Field(ge=1)


class DispatchRequest(WireModel):
    preparation_id: str = Field(min_length=1, max_length=36)
    prepared_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_engineering_revision: int = Field(ge=1)
    authorize_print: bool
    external_spool_confirmed: bool


class FabricationFacade:
    def __init__(self, preparations, *, bambu=None):
        self.preparations, self.bambu = preparations, bambu

    def query(self, actor, operation, params):
        project = params['projectId']
        dispatch = operation in {'print_dispatches', 'print_dispatch'}
        if operation in {'print_preparations', 'print_dispatches'}:
            return {'dispatches' if dispatch else 'preparations': self.preparations.list(actor, project, dispatch=dispatch)}
        if operation in {'print_preparation', 'print_dispatch'}:
            return self.preparations.get(actor, project, params['ident'], dispatch=dispatch)
        raise DomainError('Unknown fabrication query', 'not_found', 404)

    async def action(self, actor, operation, params, body, idempotency_key=None):
        project = params['projectId']
        key = idempotency_key or str(uuid4())
        if operation == 'prepare_print':
            request = PrepareRequest.model_validate(body).model_dump(mode='json', by_alias=True, exclude_none=True)
            return self.preparations.create(actor, project, request, key)['preparation']
        if operation == 'delete_print_preparation':
            WireModel.model_validate(body)
            return self.preparations.cancel(actor, project, params['ident'], key)['preparation']
        if operation == 'complete_print_preparation':
            # Client-supplied measurements cannot establish trusted slicer provenance.
            self.preparations.get(actor, project, params['ident'])
            raise DomainError('Completion is recorded by the isolated slicer worker after verification',
                              'untrusted_preparation', 409)
        if operation == 'create_print_dispatch':
            request = DispatchRequest.model_validate(body).model_dump(mode='json', by_alias=True)
            # Even authorizePrint=true cannot override server physical policy.
            serial = 'unpaired'
            if self.bambu is not None:
                access = self.bambu._local(actor, params)
                state = self.bambu.connections.state(access)
                if state.serial is not None:
                    serial = state.serial
            return self.preparations.dispatch(actor, project, request, key, printer_serial=serial)['dispatch']
        if operation in {'print_send', 'print_cancel', 'print_reconcile'}:
            WireModel.model_validate(body)
            current = self.preparations.get(actor, project, params['ident'], dispatch=True)
            with self.preparations.projects.transaction() as session:
                self.preparations.projects.authorized(session, actor, project, write=True)
            if operation == 'print_send':
                raise DomainError('Printer upload and start are blocked by server policy', 'physical_policy', 403)
            # Nothing was sent; report the durable blocked dispatch without claiming cancellation on hardware.
            return current
        raise DomainError('Unknown fabrication action', 'not_found', 404)
