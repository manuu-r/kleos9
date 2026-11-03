"""Unchanged workbench preview shapes over current, hash-bound engineering files."""
import json
import math
import re
from pathlib import PurePosixPath

from sqlalchemy import select

from kleos.artifacts.service import Artifact, NativePublication
from kleos.contracts.models import ArtifactRef, DomainError
from kleos.execution.preview_geometry import MAX_PARTS, PreviewGeometryError, pcb_geometry, stl_mesh
from kleos.projects.composition_results import effective_artifacts
from kleos.projects.store import Objective, fingerprint
from kleos.validation.service import Release, _ref

QUERIES = {'engineering_preview', 'circuit_simulation'}
LANES = {'cad': 'mechanics', 'pcb': 'electronics', 'firmware': 'firmware', 'application': 'application'}


class EngineeringPreviews:
    def __init__(self, projects, artifacts):
        self.projects, self.artifacts = projects, artifacts
        self.companion = None
        self.simulation = None  # SimulationService: reads stored evidence, never starts compute

    def _selection(self, session, project):
        accepted = session.get(Release, project.data.get('acceptedReleaseId', ''))
        if accepted is not None and accepted.project_id == project.id and accepted.revision == project.context_revision:
            data = accepted.data
            if (data.get('projectId') != project.id or data.get('acceptedRevision') != accepted.revision
                    or data.get('manifestHash') != fingerprint({k: v for k, v in data.items() if k != 'manifestHash'})):
                raise DomainError('Accepted release binding changed', 'artifact_conflict')
            return data['sources'] + data['outputs'], True
        encoded = {}
        runs = list(session.scalars(select(Objective).where(Objective.project_id == project.id,
            Objective.revision == project.context_revision,
            Objective.status.in_(['pending', 'running', 'waiting', 'succeeded']))))
        for run in runs:
            effective = {}
            def references(task_id):
                if task_id not in effective:
                    effective[task_id] = effective_artifacts(session, run, task_id)
                return effective[task_id]
            for task_id in run.data.get('results', {}):
                if task_id in run.data.get('superseded', {}):
                    continue
                for ref in references(task_id):
                    row = session.get(Artifact, ref.id)
                    if row is None or row.project_id != project.id or _ref(row) != ref:
                        raise DomainError('Published preview artifact binding changed', 'artifact_conflict')
                    if row.details.get('origin') != 'native_candidate':
                        continue
                    producer = row.details.get('taskId')
                    if producer in run.data.get('superseded', {}):
                        continue
                    if (row.revision != project.context_revision or row.details.get('runId') != run.id
                            or row.status != 'candidate' or producer not in run.data.get('results', {})):
                        raise DomainError('Preview artifact is outside the current task', 'artifact_scope_conflict')
                    # A validation task may echo producer refs, but cannot make
                    # historical authored outputs current after compilation.
                    if producer != task_id and ref not in references(producer):
                        continue
                    receipt = session.scalar(select(NativePublication).where(NativePublication.run_id == run.id,
                        NativePublication.task_id == producer, NativePublication.job_id == row.details.get('nativeJobId')))
                    if receipt is None or ref.model_dump(mode='json') not in receipt.references:
                        raise DomainError('Preview artifact lacks its publication binding', 'artifact_conflict')
                    encoded[ref.id] = ref.model_dump(mode='json')
                    if len(encoded) > 256:
                        raise DomainError('Preview artifact count exceeds bounds', 'artifact_limit', 413)
        return list(encoded.values()), False

    def query(self, actor, operation, params):
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, actor, params['projectId'])
            header = {'revision': project.revision, 'engineeringRevision': project.context_revision}
            if operation == 'circuit_simulation':
                if self.simulation is not None:
                    return self.simulation.current(session, project)
                return {**header, 'status': 'UNKNOWN', 'artifactId': None, 'artifactSha256': None,
                        'result': None, 'reason': 'No independent circuit simulation is qualified for these artifacts.'}
            if operation != 'engineering_preview':
                raise DomainError('Unknown preview query', 'not_found', 404)
            original_context = project.context_revision
            selected, accepted = self._selection(session, project)
            if len(selected) > 256:
                raise DomainError('Preview artifact count exceeds bounds', 'artifact_limit', 413)
            rows = []
            for item in selected:
                ref = ArtifactRef.model_validate(item)
                row = self.artifacts._authorized(session, actor, ref.id, project.id)
                if _ref(row) != ref:
                    raise DomainError('Preview artifact binding changed', 'artifact_conflict')
                rows.append(row)
            result = {**header, 'files': [], 'limitations': [
                'Engineering previews show file geometry and source text. They do not establish physical behavior or acceptance.']}
            for lane in LANES.values():
                result[lane] = {'source': 'unavailable', 'status': 'UNKNOWN', 'limitations': [],
                                'summary': 'No current published engineering artifacts are available.'}
            total = sum(row.size for row in rows)
            if total > 64 * 1024**2:
                raise DomainError('Preview artifact closure exceeds bounds', 'artifact_limit', 413)
            # Hash verification occurs before parsing; authorization/revision decisions
            # and data selection stay in the same project transaction.
            content = {}
            for row in rows:
                if row.lane not in LANES:
                    continue
                result['files'].append({'id': row.id, 'name': row.name, 'lane': LANES[row.lane],
                    'status': 'accepted' if accepted else row.status, 'sha256': row.sha256})
                raw = self.artifacts.blobs.get(row.sha256)
                if len(raw) != row.size:
                    raise DomainError('Preview artifact size changed', 'artifact_conflict')
                content[row.id] = raw
            for lane, key in LANES.items():
                selected_rows = [row for row in rows if row.lane == lane]
                view = result[key]
                sources = [row for row in selected_rows if row.details.get('role') == 'source' and row.id in content and row.size <= 1024**2]
                if sources:
                    preferred = {'cad': 'model.py', 'pcb': 'board.kicad_pcb',
                                 'firmware': 'src/main.c', 'application': 'lib/main.dart'}[lane]
                    sources.sort(key=lambda row: (row.name != preferred, row.name, row.id))
                    source = sources[0]
                    try:
                        code = content[source.id].decode('utf8')
                    except UnicodeDecodeError:
                        code = ''
                    if code:
                        view.update(code=code[:12000], codeArtifactId=source.id, codeSha256=source.sha256,
                                    codeName=source.name, codeStatus='accepted' if accepted else source.status,
                                    codeTruncated=len(code) > 12000)
                try:
                    if lane == 'cad':
                        self._cad(view, selected_rows, content, accepted)
                    elif lane == 'pcb':
                        self._pcb(view, selected_rows, content, accepted)
                    elif lane == 'application':
                        view['limitations'] = ['Compiled application hosting requires the separate preview-origin service.']
                        view['previewAvailable'] = False
                        bundles = [row for row in selected_rows if row.name == 'outputs/companion-web.zip'
                                   and row.details.get('role') == 'output']
                        if self.companion and len(bundles) == 1:
                            try:
                                view.update(self.companion.metadata(bundles[0], content[bundles[0].id]))
                            except DomainError:
                                view['limitations'] = ['The application bundle cannot be served by the bounded preview host.']
                            if view['previewAvailable']:
                                view.update(source='artifact', status='accepted' if accepted else 'candidate',
                                    summary='Built companion application available through the isolated preview host.',
                                    limitations=['Sandboxed web preview; device behavior and engineering acceptance are unverified.'])
                except PreviewGeometryError as error:
                    view.update(status='UNKNOWN', source='unavailable', limitations=[str(error)],
                                summary='The current engineering files cannot be represented by this bounded viewer.')
            session.expire_all()
            current = self.projects.authorized(session, actor, params['projectId'])
            if current.context_revision != original_context:
                raise DomainError('Engineering context changed during preview', 'revision_conflict')
            return result

    def _cad(self, view, rows, content, accepted):
        stls = sorted([row for row in rows if row.details.get('role') == 'output' and row.name.endswith('.stl')],
                      key=lambda row: (row.name, row.id))
        if not stls:
            return
        if len(stls) > MAX_PARTS:
            raise PreviewGeometryError('CAD part count exceeds preview bounds')
        part_ids, parts = set(), []
        for row in stls:
            ident = PurePosixPath(row.name).stem
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', ident) or ident in part_ids:
                raise PreviewGeometryError('CAD part identity is invalid or ambiguous across current tasks')
            part_ids.add(ident)
            paired = [other for other in rows if other.name == str(PurePosixPath(row.name).with_suffix('.step'))
                      and other.details.get('nativeJobId') == row.details.get('nativeJobId')]
            if len(paired) != 1:
                raise PreviewGeometryError('CAD preview requires an exact matching STEP/STL part pair')
            mesh = stl_mesh(content[row.id], triangle_limit=min(1800, 12000 // len(stls)))
            parts.append({'id': ident, 'label': ident, 'quantity': 1, 'mesh': mesh,
                          'artifactId': row.id, 'sha256': row.sha256, 'stepArtifactId': paired[0].id,
                          'stepSha256': paired[0].sha256,
                          'transform': {'translation': {'x': 0, 'y': 0, 'z': 0}, 'rotation': {'x': 0, 'y': 0, 'z': 0}}})
        for row in rows:
            if row.name == 'outputs/parts.json' and row.id in content:
                try:
                    if len(content[row.id]) > 64 * 1024:
                        raise ValueError()
                    manifest = json.loads(content[row.id])
                    entries = manifest['parts']
                    expected = {PurePosixPath(part.name).stem for part in stls
                                if part.details.get('nativeJobId') == row.details.get('nativeJobId')}
                    if (not isinstance(entries, list) or len(entries) != len(expected)
                            or {entry['id'] for entry in entries} != expected
                            or any(entry['stl'] != entry['id']+'.stl' or entry['step'] != entry['id']+'.step' for entry in entries)):
                        raise ValueError()
                except (TypeError, KeyError, ValueError):
                    raise PreviewGeometryError('CAD part manifest does not match published part files') from None
        centers = [[(part['mesh']['bounds']['min'][i]+part['mesh']['bounds']['max'][i])/2 for i in range(3)] for part in parts]
        center = [sum(point[i] for point in centers)/len(centers) for i in range(3)]
        extent = max(1, max(math.dist(point, center) for part in parts for point in part['mesh']['bounds'].values()))
        for index, part in enumerate(parts):
            direction = [centers[index][i]-center[i] for i in range(3)]
            length = math.sqrt(sum(value*value for value in direction))
            part['displayExplodeMm'] = ([value*extent*.7/length for value in direction] if length > .01
                else [0, 0, (index-(len(parts)-1)/2)*extent*.8])
        view.update(source='artifact', status='accepted' if accepted else 'candidate',
                    name='Generated CAD assembly', artifactId=stls[0].id,
                    assembly={'parts': parts, 'units': 'mm', 'coordinateSpace': 'manufacturing', 'explosionDisplayOnly': True},
                    summary=f'{len(parts)} separate physical parts from published STEP/STL files.',
                    limitations=['Mesh sampling and exploded translations are display-only; STEP/STL coordinates are unchanged.'])

    def _pcb(self, view, rows, content, accepted):
        boards = [row for row in rows if row.name == 'outputs/routed.kicad_pcb' and row.details.get('role') == 'output']
        if not boards:
            return
        if len(boards) != 1:
            raise PreviewGeometryError('Multiple current boards require an explicit board selection')
        row = boards[0]
        pcb = pcb_geometry(content[row.id])
        view.update(source='artifact', status='accepted' if accepted else 'candidate', name=row.name,
                    artifactId=row.id, sha256=row.sha256, pcb=pcb, limitations=pcb['limitations'],
                    summary='Board dimensions, component pad extents and straight copper segments read from the routed KiCad artifact.')
