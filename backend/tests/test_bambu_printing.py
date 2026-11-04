# ruff: noqa: F811
"""Authorized upload and start on a paired X1 Carbon, with a fake printer: state machine, safety gates, no resend."""
import asyncio
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_slicing import ACTOR, prepared  # noqa: F401  (fixture)

from kleos.api.fabrication import FabricationFacade
from kleos.capabilities.fabrication import BambuConnections, LocalAccess, PairRequest
from kleos.capabilities.fabrication.print_transport import project_command
from kleos.capabilities.fabrication.printing import PrintDispatcher
from kleos.capabilities.fabrication.transport import ConnectionFailure
from kleos.contracts.models import DomainError

ACCESS = LocalAccess(ACTOR, True)
CERT = hashlib.sha256(b'fixture-printer-certificate').hexdigest()


class Printer:
    """Fake status and write transports sharing one printer state."""

    def __init__(self):
        self.state, self.uploads, self.starts = 'idle', [], []
        self.upload_error = self.start_error = None
        self.confirm = True

    def observe(self, request, fingerprint):
        return {'kind': 'bambu', 'connected': True, 'state': self.state, 'profile': {'physicalAcceptance': False},
                'observedAt': datetime.now(UTC).isoformat()}

    def upload(self, request, fingerprint, name, data):
        self.uploads.append((fingerprint, name, data))
        if self.upload_error:
            raise self.upload_error

    def start(self, request, fingerprint, command):
        self.starts.append((fingerprint, command))
        if self.start_error:
            raise self.start_error
        return {'confirmed': self.confirm, 'state': 'running' if self.confirm else 'unknown'}


def pair(model='BL-P001'):
    return PairRequest(address='192.168.1.33', serial='SERIAL123', accessCode='ABCD1234', modelId=model,
                       certificateSha256=CERT, certificateVerified=True, ftpsCertificateSha256='b' * 64,
                       ftpsCertificateVerified=True)


async def compose(prepared, *, models=frozenset({'BL-P001'}), model='BL-P001', profile='x1c-pla-04'):
    service, project, body, driver = prepared
    printer = Printer()
    connections = BambuConnections(enabled=True, transport=printer, printing_models=models)
    await connections.pair(ACCESS, pair(model))
    dispatcher = PrintDispatcher(service, connections, printer, models=models)
    facade = FabricationFacade(service, bambu=SimpleNamespace(connections=connections, _local=lambda *_: ACCESS),
                               dispatcher=dispatcher)
    view = service.create(ACTOR, project, {**body, 'profileId': profile}, 'prepare')['preparation']
    await service.process_next()
    await service.reconcile()
    ready = service.get(ACTOR, project, view['id'])
    assert ready['status'] == 'ready_for_operator', ready
    return facade, printer, connections, service, project, ready


def dispatch_body(ready, **changes):
    return {'preparationId': ready['id'], 'preparedSha256': ready['preparedSha256'], 'expectedEngineeringRevision': 1,
            'authorizePrint': True, 'externalSpoolConfirmed': True, **changes}


async def authorize(facade, project, ready, key='dispatch', **changes):
    return await facade.action(ACTOR, 'create_print_dispatch', {'projectId': project, '_transport': None},
                               dispatch_body(ready, **changes), key)


async def act(facade, project, dispatch, operation, body=None):
    return await facade.action(ACTOR, operation, {'projectId': project, 'ident': dispatch['id']}, body or {}, None)


@pytest.mark.asyncio
async def test_printing_stays_blocked_unless_the_operator_enabled_it(prepared):
    facade, printer, _, _, project, ready = await compose(prepared, models=frozenset())
    dispatch = await authorize(facade, project, ready)
    assert dispatch['status'] == 'blocked' and 'server policy' in dispatch['message']
    with pytest.raises(DomainError) as error:
        await act(facade, project, dispatch, 'print_send')
    assert error.value.status == 403 and not printer.uploads and not printer.starts


@pytest.mark.asyncio
async def test_an_authorized_print_uploads_the_exact_plate_then_starts_once(prepared):
    facade, printer, _, service, project, ready = await compose(prepared)
    dispatch = await authorize(facade, project, ready)
    assert dispatch['status'] == 'authorized' and not printer.uploads  # authorizing sends nothing
    result = await act(facade, project, dispatch, 'print_send')
    assert result['status'] == 'printing' and result['physicalAcceptance'] is False
    ((pin, name, data),) = printer.uploads
    assert pin == 'b' * 64 and name.startswith('kleos-') and name.endswith('.3mf')
    assert hashlib.sha256(data).hexdigest() == ready['preparedSha256']
    ((fingerprint, command),) = printer.starts
    assert fingerprint == CERT and command == project_command(name)
    with pytest.raises(DomainError) as again:  # a sent print can never be sent again
        await act(facade, project, dispatch, 'print_send')
    assert again.value.code == 'dispatch_state' and len(printer.uploads) == len(printer.starts) == 1


@pytest.mark.asyncio
async def test_an_unconfirmed_start_is_reported_as_requested_not_printing(prepared):
    facade, printer, _, _, project, ready = await compose(prepared)
    printer.confirm = False
    result = await act(facade, project, await authorize(facade, project, ready), 'print_send')
    assert result['status'] == 'start_requested' and 'not yet been confirmed' in result['message']


@pytest.mark.asyncio
async def test_a_failed_upload_starts_nothing_and_cannot_be_retried_on_the_same_dispatch(prepared):
    facade, printer, _, _, project, ready = await compose(prepared)
    printer.upload_error = ConnectionFailure('bambu_unreachable')
    dispatch = await authorize(facade, project, ready)
    with pytest.raises(DomainError) as error:
        await act(facade, project, dispatch, 'print_send')
    assert error.value.code == 'bambu_unreachable' and not printer.starts
    failed = facade.preparations.get(ACTOR, project, dispatch['id'], dispatch=True)
    assert failed['status'] == 'failed' and 'Nothing was started' in failed['message']
    with pytest.raises(DomainError):
        await act(facade, project, dispatch, 'print_send')
    assert len(printer.uploads) == 1


@pytest.mark.asyncio
async def test_a_lost_start_reply_is_uncertain_and_the_start_is_never_resent(prepared):
    facade, printer, _, _, project, ready = await compose(prepared)
    printer.start_error = TimeoutError()
    dispatch = await authorize(facade, project, ready)
    with pytest.raises(DomainError) as error:
        await act(facade, project, dispatch, 'print_send')
    assert error.value.code == 'dispatch_uncertain'
    assert facade.preparations.get(ACTOR, project, dispatch['id'], dispatch=True)['status'] == 'uncertain'
    printer.start_error = None
    with pytest.raises(DomainError):
        await act(facade, project, dispatch, 'print_send')
    assert len(printer.starts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['running', 'pause', 'prepare', 'unknown'])
async def test_a_busy_or_unknown_printer_gets_nothing_and_the_authorization_stays(prepared, phase):
    facade, printer, _, _, project, ready = await compose(prepared)
    dispatch = await authorize(facade, project, ready)
    printer.state = phase
    with pytest.raises(DomainError) as error:
        await act(facade, project, dispatch, 'print_send')
    assert error.value.code == 'printer_not_ready' and not printer.uploads and not printer.starts
    printer.state = 'idle'
    assert (await act(facade, project, dispatch, 'print_send'))['status'] == 'printing'


@pytest.mark.asyncio
async def test_two_concurrent_sends_upload_once(prepared):
    facade, printer, _, _, project, ready = await compose(prepared)
    dispatch = await authorize(facade, project, ready)
    results = await asyncio.gather(act(facade, project, dispatch, 'print_send'),
                                   act(facade, project, dispatch, 'print_send'), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in results) == 1
    assert len(printer.uploads) == len(printer.starts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'authorizePrint': False}, {'externalSpoolConfirmed': False}])
async def test_unconfirmed_bed_or_spool_is_not_authorization(prepared, changes):
    facade, printer, _, _, project, ready = await compose(prepared)
    dispatch = await authorize(facade, project, ready, **changes)
    assert dispatch['status'] == 'blocked' and 'Confirm' in dispatch['message']
    with pytest.raises(DomainError):
        await act(facade, project, dispatch, 'print_send')
    assert not printer.uploads


@pytest.mark.asyncio
async def test_only_the_x1_carbon_is_enabled_and_the_plate_must_match_it(prepared):
    facade, printer, _, _, project, ready = await compose(prepared, model='C12', profile='p1s-pla-04')
    dispatch = await authorize(facade, project, ready)
    assert dispatch['status'] == 'blocked' and 'enabled for printing' in dispatch['message']
    assert not printer.uploads


@pytest.mark.asyncio
async def test_a_stale_preparation_cannot_be_sent(prepared):
    from kleos.projects.store import Project
    facade, printer, _, service, project, ready = await compose(prepared)
    dispatch = await authorize(facade, project, ready)
    with service.projects.transaction() as session:
        record = session.get(Project, project)
        record.data = {**record.data, 'engineeringRevision': 2}
    with pytest.raises(DomainError) as error:
        await act(facade, project, dispatch, 'print_send')
    assert error.value.code == 'revision_conflict' and not printer.uploads


@pytest.mark.asyncio
async def test_cancel_withdraws_an_authorization_but_never_claims_to_stop_a_sent_print(prepared):
    facade, printer, _, _, project, ready = await compose(prepared)
    waiting = await authorize(facade, project, ready)
    assert (await act(facade, project, waiting, 'print_cancel'))['status'] == 'cancelled'
    with pytest.raises(DomainError):
        await act(facade, project, waiting, 'print_send')
    assert not printer.uploads
    sent = await authorize(facade, project, ready, key='second')
    await act(facade, project, sent, 'print_send')
    cancelled = await act(facade, project, sent, 'print_cancel')
    assert cancelled['status'] == 'cancelled_uncertain' and 'not stopped' in cancelled['message']
    assert len(printer.starts) == 1  # no stop command exists


@pytest.mark.asyncio
async def test_reconcile_records_a_person_confirmed_outcome_and_refuses_a_busy_printer(prepared):
    facade, printer, connections, _, project, ready = await compose(prepared)
    dispatch = await authorize(facade, project, ready)
    await act(facade, project, dispatch, 'print_send')
    with pytest.raises(DomainError) as unconfirmed:
        await act(facade, project, dispatch, 'print_reconcile', {'outcome': 'finished'})
    assert unconfirmed.value.status == 422
    printer.state = 'running'
    await connections.refresh(ACCESS)
    with pytest.raises(DomainError) as busy:
        await act(facade, project, dispatch, 'print_reconcile', {'confirmPrinterIdle': True, 'outcome': 'finished'})
    assert busy.value.code == 'printer_busy'
    printer.state = 'idle'
    await connections.refresh(ACCESS)
    done = await act(facade, project, dispatch, 'print_reconcile', {'confirmPrinterIdle': True, 'outcome': 'finished'})
    assert done['status'] == 'finished' and 'not physical acceptance' in done['message'] and done['physicalAcceptance'] is False
