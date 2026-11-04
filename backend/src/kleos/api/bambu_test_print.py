"""Local fixed-tile preparation over the existing isolated fabrication queue."""
import math
import struct
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from kleos.api import Content
from kleos.artifacts.service import Artifact
from kleos.capabilities.fabrication.slicing import PROTOCOL, PrintPreparation, digest, validate_package
from kleos.contracts.models import DomainError, WireModel
from kleos.projects.store import Membership, Project, fingerprint

PURPOSE = 'bambu_test_print'
ACTIONS = {'bambu_prepare_test'}
QUERIES = {'bambu_test_print', 'bambu_catalog'}
CONTENTS = {'bambu_test_source', 'bambu_test_plate'}


class TestPrintRequest(WireModel):
    profile_id: str = Field(min_length=1, max_length=100)
    printer_model_id: str = Field(min_length=1, max_length=80)


def tile_stl():
    """Serialize one fixed trusted fixture, not user/model supplied geometry code."""
    vertices = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
                (0, 0, 2), (10, 0, 2), (10, 10, 2), (0, 10, 2)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    result = bytearray(b'Kleos fixed 10 x 10 x 2 mm digital test tile v1'.ljust(80, b'\0'))
    result.extend(struct.pack('<I', len(faces)))
    for face in faces:
        a, b, c = [vertices[index] for index in face]
        u, v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
        normal = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        length = math.sqrt(sum(value*value for value in normal))
        result.extend(struct.pack('<12fH', *[value/length for value in normal], *a, *b, *c, 0))
    return bytes(result)


class BambuTestPrintFacade:
    def __init__(self, bambu, preparations):
        self.bambu, self.preparations = bambu, preparations
        self.projects, self.blobs = preparations.projects, preparations.blobs
        self.source = tile_stl()
        self.source_hash = digest(self.source)

    @staticmethod
    def _project_id(actor):
        return str(uuid5(NAMESPACE_URL, 'kleos:bambu-test:'+fingerprint([actor.tenant_id, actor.user_id])))

    def _fixture(self, session, actor):
        project = session.get(Project, self._project_id(actor), with_for_update=True)
        if (project is None or project.tenant_id != actor.tenant_id or project.owner_id != actor.user_id
                or project.data.get('internalPurpose') != PURPOSE
                or project.context_revision != 1 or project.data.get('engineeringRevision') != 1):
            raise DomainError('Test preparation not found', 'not_found', 404)
        member = session.get(Membership, (project.id, actor.user_id))
        source = session.get(Artifact, project.data.get('testSourceId'))
        if (member is None or member.role != 'owner' or source is None
                or source.project_id != project.id or source.sha256 != self.source_hash
                or source.size != len(self.source) or source.name != 'kleos-test-tile.stl'
                or source.details.get('origin') != PURPOSE
                or project.data.get('sourceManifest') != {source.name: source.id}):
            raise DomainError('Test fixture identity changed', 'artifact_conflict')
        return project, source

    def _row(self, session, actor, ident):
        project, source = self._fixture(session, actor)
        row = session.get(PrintPreparation, ident)
        if (row is None or row.tenant_id != actor.tenant_id or row.creator_id != actor.user_id
                or row.project_id != project.id or row.source_artifact_id != source.id
                or row.source_sha256 != self.source_hash or row.engineering_revision != 1):
            raise DomainError('Test preparation not found', 'not_found', 404)
        return row, source

    @staticmethod
    def _number(value):
        return value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None

    def _view(self, row):
        status = 'slicing' if row.status in {'queued', 'running'} else 'ready' if row.status == 'ready' else 'failed'
        message = {'slicing': 'Waiting for the isolated slicer. No printer command has been sent.',
            'ready': 'Digital test plate prepared. Review before any operator-managed print; no printer command has been sent.',
            'failed': 'Test preparation did not complete. No printer command has been sent; uncertain work is retained for reconciliation.'}[status]
        return {'id': row.id, 'status': status, 'printerModelId': row.profile['printerModelId'],
            'profileId': row.profile_id, 'profile': row.profile, 'dimensionsMm': [10, 10, 2],
            'sourceSha256': row.source_sha256, 'packageSha256': row.prepared_sha256 if status == 'ready' else None,
            'estimatedSeconds': self._number(row.measurements.get('estimatedSeconds')) if status == 'ready' else None,
            'filamentGrams': self._number(row.measurements.get('filamentGrams')) if status == 'ready' else None,
            'filename': 'kleos-test-tile.gcode.3mf', 'message': message, 'physicalAcceptance': False}

    async def action(self, actor, operation, params, body, idempotency_key=None):
        self.bambu._local(actor, params)
        if operation not in ACTIONS:
            raise DomainError('Unknown test preparation operation', 'not_found', 404)
        request = TestPrintRequest.model_validate(body)
        if self.preparations.settings is None or self.preparations.driver is None:
            raise DomainError('No qualified isolated slicer is configured', 'capability_unavailable', 501)
        profile = self.preparations.settings.profile(request.profile_id)
        if request.printer_model_id != profile['printerModelId']:
            raise DomainError('The installed profile does not match the selected printer model', 'profile_conflict', 422)
        key = idempotency_key or 'bambu-test:'+fingerprint([request.model_dump(mode='json'), profile, self.source_hash])
        self.blobs.put(self.source)

        def create(session):
            self.bambu.access._member(session, actor, admin=True)
            project_id = self._project_id(actor)
            project = session.get(Project, project_id, with_for_update=True)
            if project is None:
                source_id = str(uuid5(NAMESPACE_URL, project_id+':tile-v1'))
                project = Project(id=project_id, tenant_id=actor.tenant_id, owner_id=actor.user_id,
                    revision=1, event_sequence=0, data={'internalPurpose': PURPOSE, 'testSourceId': source_id,
                        'brief': 'Private fixed test tile for local offline slicing', 'name': 'Local slicer test',
                        'createdAt': datetime.now(UTC).isoformat(), 'contextRevision': 1, 'engineeringRevision': 1,
                        'sourceManifest': {'kleos-test-tile.stl': source_id}, 'requirements': {}, 'interfaces': {},
                        'approvedDesign': None, 'decisions': []})
                session.add(project)
                session.add(Membership(project_id=project_id, user_id=actor.user_id, role='owner'))
                session.add(Artifact(id=source_id, project_id=project_id, revision=1, name='kleos-test-tile.stl',
                    sha256=self.source_hash, size=len(self.source), media_type='model/stl', status='input',
                    lane='fabrication', details={'origin': PURPOSE, 'fixtureVersion': 1, 'physicalAcceptance': False}))
                session.flush()
            project, source = self._fixture(session, actor)
            pending = session.scalar(select(func.count()).select_from(PrintPreparation).where(
                PrintPreparation.project_id == project.id, PrintPreparation.status.in_(['queued', 'running'])))
            if pending >= 4:
                raise DomainError('Too many pending local test preparations', 'capacity_exceeded', 429)
            row = PrintPreparation(id=str(uuid4()), tenant_id=actor.tenant_id, creator_id=actor.user_id,
                project_id=project.id, engineering_revision=1, status='queued', source_artifact_id=source.id,
                source_sha256=self.source_hash, profile_id=request.profile_id, profile=profile,
                measurements={}, generation=0, created_at=self.preparations.clock())
            session.add(row)
            self.projects._emit(session, project, 'bambu.test_preparation.created',
                {'id': row.id, 'profileId': row.profile_id, 'physicalAcceptance': False})
            # Internal project identity is intentionally absent from the public test-print DTO.
            return {'testPreparationId': row.id}

        command = {'operation': 'bambu.prepareTest', **request.model_dump(mode='json'),
            'profile': profile, 'sourceSha256': self.source_hash}
        try:
            receipt = self.projects._command(actor, key, command, create)
        except IntegrityError:
            # PostgreSQL commands already serialize per actor. SQLite can race on
            # the deterministic fixture/receipt identity; the losing transaction
            # has rolled back and no native effect was dispatched. Retry once to
            # load that winner, preserving the same request identity.
            receipt = self.projects._command(actor, key, command, create)
        self.bambu._local(actor, params)
        with self.projects.transaction() as session:
            row, _ = self._row(session, actor, receipt['testPreparationId'])
            return self._view(row)

    def query(self, actor, operation, params):
        if operation == 'bambu_catalog':
            result = self.bambu.query(actor, operation, params)
            for profile in result['profiles']:
                profile['testPreparation'] = self.preparations.settings is not None and self.preparations.driver is not None
            return result
        self.bambu._local(actor, params)
        if operation != 'bambu_test_print':
            raise DomainError('Unknown test preparation query', 'not_found', 404)
        with self.projects.transaction() as session:
            row, _ = self._row(session, actor, params.get('ident'))
            return self._view(row)

    def content(self, actor, operation, params):
        self.bambu._local(actor, params)
        if operation not in CONTENTS:
            raise DomainError('Unknown test preparation download', 'not_found', 404)
        with self.projects.transaction() as session:
            row, source = self._row(session, actor, params.get('ident'))
            if operation == 'bambu_test_source':
                raw = self.blobs.get(source.sha256)
                if raw != self.source:
                    raise DomainError('Test source integrity failed', 'artifact_conflict')
                return Content(raw, 'model/stl', 'kleos-test-tile.stl')
            if row.status != 'ready' or not row.prepared_artifact_id or not row.prepared_sha256:
                raise DomainError('The sliced test plate is not ready', 'preparation_not_ready')
            artifact = session.get(Artifact, row.prepared_artifact_id)
            if (artifact is None or artifact.project_id != row.project_id or artifact.sha256 != row.prepared_sha256
                    or artifact.details.get('origin') != PROTOCOL or artifact.details.get('preparationId') != row.id
                    or artifact.details.get('sourceSha256') != row.source_sha256 or artifact.details.get('profile') != row.profile):
                raise DomainError('Test plate provenance differs from its preparation', 'artifact_conflict')
            raw = self.blobs.get(artifact.sha256)
            if len(raw) != artifact.size:
                raise DomainError('Test plate size differs from its artifact', 'artifact_conflict')
            try:
                validate_package(raw)
            except (ValueError, OSError):
                raise DomainError('Test plate archive could not be verified', 'artifact_conflict') from None
            return Content(raw, 'application/zip', 'kleos-test-tile.gcode.3mf')
