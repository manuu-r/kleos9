"""Authorized upload and start of a sliced plate on a paired Bambu printer (X1 Carbon).

This is the only code that sends a file or a command to a printer. It is off unless the operator enables printing, and
then every print needs: a ready preparation that is still current, an explicit authorization from the user who
confirmed the bed and spool, a paired and idle printer of the model the plate was sliced for, and a local
administrator session. State is committed before each irreversible step, so a crash or a lost reply leaves the
dispatch `uncertain` rather than sending anything twice. The start command is never resent, and nothing here
claims the part was printed: a person confirms the outcome at the printer.
"""
import asyncio
import hashlib

from sqlalchemy import select

from kleos.contracts.models import DomainError
from kleos.projects.store import Project

from .print_transport import BambuPrintTransport, project_command
from .slicing import PrintDispatch, PrintPreparation, current_source, dispatch_view
from .transport import ERRORS, ConnectionFailure

IN_FLIGHT = {'uploading', 'uploaded', 'start_requested', 'printing', 'uncertain', 'cancelled_uncertain'}
IDLE_STATES = {'idle', 'finish'}
OUTCOMES = {'not_started': 'not_started', 'finished': 'finished', 'stopped': 'stopped'}
POLICY_MESSAGE = 'Printer upload and start are blocked by server policy.'


class PrintDispatcher:
    def __init__(self, preparations, connections, transport=None, *, models=frozenset()):
        self.preparations, self.connections = preparations, connections
        self.transport = transport or BambuPrintTransport()
        self.models = frozenset(models)

    @property
    def enabled(self):
        return bool(self.models)

    def authorization(self, access, preparation, request):
        """('authorized', message) when this print may be sent, else ('blocked', reason). Nothing is sent here."""
        if not self.enabled:
            return 'blocked', POLICY_MESSAGE
        if not (request['authorizePrint'] and request['externalSpoolConfirmed']):
            return 'blocked', 'Confirm the bed is clear and the filament is on the external spool to authorize a print.'
        profile = preparation['profile']
        if profile.get('qualified') is not True:
            return 'blocked', 'This slicing profile is not qualified for printing.'
        state = self.connections.state(access)
        if not (state.paired and state.connected and state.serial):
            return 'blocked', 'Pair the printer and refresh its status first.'
        if state.model_id not in self.models or profile.get('printerModelId') != state.model_id:
            return 'blocked', 'The sliced plate does not match a printer that is enabled for printing.'
        return 'authorized', 'Authorized. The plate has not been sent to the printer yet.'

    def _transition(self, ident, expected, status, message):
        """Compare-and-set a dispatch status in its own transaction so two senders cannot both proceed."""
        with self.preparations.projects.transaction() as session:
            row = session.scalar(select(PrintDispatch).where(PrintDispatch.id == ident).with_for_update())
            if row is None:
                raise DomainError('Print job not found', 'not_found', 404)
            if row.status not in expected:
                raise DomainError('This print is not in a state that allows that action', 'dispatch_state', 409)
            row.status, row.message = status, message[:300]
            self.preparations.projects._emit(session, session.get(Project, row.project_id),
                                             'print.dispatch.' + status, dispatch_view(row))
            return dispatch_view(row)

    async def send(self, access, actor, project_id, ident):
        preparations = self.preparations
        if not self.enabled:
            raise DomainError('Printer upload and start are blocked by server policy', 'physical_policy', 403)
        with preparations.projects.transaction() as session:
            project, row = preparations._row(session, actor, project_id, ident, dispatch=True, write=True)
            if row.status != 'authorized':
                raise DomainError('Only an authorized print can be sent', 'dispatch_state', 409)
            prep = session.get(PrintPreparation, row.preparation_id)
            from kleos.artifacts.service import Artifact
            if (prep is None or prep.status != 'ready' or prep.prepared_sha256 != row.prepared_sha256
                    or project.data.get('engineeringRevision', 1) != prep.engineering_revision
                    or not current_source(session, project, session.get(Artifact, prep.source_artifact_id), prep.source_sha256)):
                raise DomainError('Print preparation is no longer current', 'revision_conflict', 409)
            sliced_for = prep.profile.get('printerModelId')
            checksum = prep.prepared_sha256
        pairing = self.connections.pairing(access)
        if pairing.request.model_id not in self.models or sliced_for != pairing.request.model_id:
            raise DomainError('The paired printer does not match the sliced plate', 'printer_mismatch', 409)
        state = await self.connections.refresh(access)
        phase = str(state.observation.get('state', 'unknown')) if state.connected else 'unknown'
        if not state.connected or phase not in IDLE_STATES:
            raise DomainError('The printer must be connected and idle before a print is sent', 'printer_not_ready', 409)
        package = preparations.blobs.get(checksum)
        if hashlib.sha256(package).hexdigest() != checksum:
            raise DomainError('The sliced plate differs from its recorded hash', 'result_conflict', 409)
        request = pairing.request
        name = f'kleos-{ident.replace("-", "")[:12]}.3mf'
        ftps_pin = request.ftps_certificate_sha256
        # The first irreversible step is claimed durably; a concurrent sender is rejected here.
        self._transition(ident, {'authorized'}, 'uploading', 'Uploading the sliced plate to the printer.')
        try:
            if ftps_pin is None:
                identity = await asyncio.to_thread(self.connections.verifier, request.address, request.serial, 'ftps')
                if not identity.verified or identity.address != request.address or identity.service != 'ftps':
                    raise ConnectionFailure('bambu_tls_failed')
                ftps_pin = identity.certificate_sha256
            await asyncio.to_thread(self.transport.upload, request, ftps_pin, name, package)
        except Exception as exc:  # nothing was started, so this dispatch is simply failed
            code = exc.code if isinstance(exc, ConnectionFailure) else 'bambu_tls_failed'
            self._transition(ident, {'uploading'}, 'failed', 'Upload failed: ' + ERRORS.get(code, 'Printer upload failed.')
                             + ' Nothing was started.')
            raise DomainError('The upload to the printer failed; nothing was started', code, 502) from None
        self._transition(ident, {'uploading'}, 'uploaded', 'Uploaded. Sending the start request.')
        # From here a lost reply cannot prove the printer did not start, so a failure is `uncertain`, never retried.
        self._transition(ident, {'uploaded'}, 'start_requested', 'Start request is being sent to the printer.')
        try:
            outcome = await asyncio.to_thread(self.transport.start, request, pairing.fingerprint, project_command(name))
        except Exception:
            self._transition(ident, {'start_requested'}, 'uncertain',
                'The start request outcome is uncertain. Check the printer; it will not be sent again.')
            raise DomainError('The start request outcome is uncertain; check the printer', 'dispatch_uncertain', 502) from None
        if outcome.get('confirmed'):
            return self._transition(ident, {'start_requested'}, 'printing',
                'The printer reports it is preparing or printing. This is not physical acceptance.')
        return self._transition(ident, {'start_requested'}, 'start_requested',
            'Start request sent. Printing has not yet been confirmed; check the printer.')

    def cancel(self, actor, project_id, ident):
        """Withdraw an authorization. A print already sent is never stopped by this service."""
        with self.preparations.projects.transaction() as session:
            _, row = self.preparations._row(session, actor, project_id, ident, dispatch=True, write=True)
            current = row.status
        if current == 'authorized':
            return self._transition(ident, {'authorized'}, 'cancelled', 'Authorization withdrawn. Nothing was sent.')
        if current in IN_FLIGHT - {'cancelled_uncertain'}:
            return self._transition(ident, IN_FLIGHT, 'cancelled_uncertain',
                'Cancellation recorded only. The printer was not stopped; stop it at the printer.')
        raise DomainError('This print can no longer be cancelled', 'dispatch_state', 409)

    def reconcile(self, access, actor, project_id, ident, body):
        """Record the outcome a person observed at the printer. It never moves or stops the printer."""
        outcome = OUTCOMES.get(body.get('outcome')) if isinstance(body, dict) else None
        if outcome is None or body.get('confirmPrinterIdle') is not True:
            raise DomainError('Confirm the printer is idle and choose its outcome', 'invalid_request', 422)
        with self.preparations.projects.transaction() as session:
            _, row = self.preparations._row(session, actor, project_id, ident, dispatch=True, write=True)
            if row.status not in IN_FLIGHT:
                raise DomainError('There is nothing to reconcile for this print', 'dispatch_state', 409)
        state = self.connections.state(access)
        if state.connected and str(state.observation.get('state')) in {'running', 'prepare', 'pause', 'slicing'}:
            raise DomainError('The printer reports it is busy; it cannot be recorded as idle', 'printer_busy', 409)
        return self._transition(ident, IN_FLIGHT, outcome,
            'Outcome recorded by the operator. This is not physical acceptance.')
