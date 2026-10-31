"""Bounded native edit/build/observe session, supervised outside generated code.

This is a candidate-production API, never an independent validation authority.
Its journal is a private sibling of the generated workspace, not inside the
tree that run_command makes writable by the generated-code UID.
"""
import base64
import copy
import errno
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from .contracts import GeneratedFile, JobResult
from .native_runner import (
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    WORKER_UID,
    NativeBuildError,
    NativeResourceError,
    NativeTimeoutError,
    UnsupportedTool,
    build_lane,
    run_command,
)

ACTIONS = {'read', 'patch', 'build', 'analyze', 'render', 'finish'}
DEFAULT_LIMITS = {'maxSteps': 32, 'maxSeconds': 900, 'maxPatchBytes': 1024 * 1024,
                  'maxReadBytes': 256 * 1024, 'maxSourceBytes': 16 * 1024 * 1024}
HARD_LIMITS = {**DEFAULT_LIMITS, 'maxSteps': 64, 'maxSeconds': 1800, 'maxSourceBytes': MAX_TOTAL_BYTES}
MAX_STEP_BYTES = 2 * 1024 * 1024
MAX_SOURCE_FILES = 256
RESERVED = {'outputs', 'build', 'native', '.home', '.tmp', '.git', '.dart_tool', '.cache', '.gradle',
            '.kleos-tool-request.json', 'kleos-build.json', 'companion-source.zip', 'companion-web.zip',
            'local.properties', '.flutter-plugins-dependencies', '.packages', 'GeneratedPluginRegistrant.java',
            'GeneratedPluginRegistrant.h', 'GeneratedPluginRegistrant.m', 'Generated.xcconfig', 'flutter_export_environment.sh',
            'companion-build-manifest.json'}
PREPARED_PREFIX = 'assets/kleos_fonts/'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    return json.dumps(raw, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _relative(name):
    if (not isinstance(name, str) or len(name) > 240 or not name or any(ord(char) < 32 for char in name)
            or '\\' in name or ':' in name or any(part in {'', '.', '..'} for part in name.split('/'))):
        raise ValueError('Workspace paths must be bounded plain relative file paths')
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ValueError('Absolute workspace paths are forbidden')
    return path


def _editable(name):
    path = _relative(name)
    if any(part in RESERVED or part.startswith('.session-') for part in path.parts) or name.startswith(PREPARED_PREFIX):
        raise ValueError('Build outputs, private state and preparation paths are read-only')
    return path


def _regular(path, base, exists=True):
    if base.is_symlink():
        raise ValueError('The lane workspace must not be a symlink')
    relative = path.relative_to(base)
    cursor = base
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('Symlink workspace access is forbidden')
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('Workspace access escaped the target lane')
    if path.exists() and not path.is_file():
        raise ValueError('Workspace operations require regular files')
    if path.exists() and path.stat().st_nlink != 1:
        raise ValueError('Hard-linked workspace files are forbidden')
    if exists and not path.is_file():
        raise ValueError('The requested workspace file does not exist')
    return path


def prepare_session_steps(control):
    """Publish private steps already owned by the trusted shared-control owner.

    The native supervisor is root while the controller can be UID1000. Publishing
    a root-owned0700 directory before chown would race the first model tool call.
    Prepare ownership privately, then publish it with one directory rename.
    """
    control = Path(control)
    if control.is_symlink() or not control.is_dir():
        raise ValueError('Native session control must be a plain private directory')
    owner = control.stat()
    if owner.st_uid == WORKER_UID or owner.st_mode & 0o077:
        raise ValueError('Native session control must exclude generated-code access')
    target = control / 'steps'
    if not target.exists() and not target.is_symlink():
        temporary = Path(tempfile.mkdtemp(prefix='.steps-ready-', dir=control))
        try:
            temporary.chmod(0o700)
            os.chown(temporary, owner.st_uid, owner.st_gid)
            try:
                os.rename(temporary, target)
            except OSError as exc:
                if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
        finally:
            if temporary.exists():
                temporary.rmdir()
    if target.is_symlink() or not target.is_dir():
        raise ValueError('Native session steps must be a plain private directory')
    metadata = target.stat()
    # Root intentionally has no CAP_FOWNER: chmod must precede transferring
    # ownership, and a controller-owned private directory needs no mutation.
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        if metadata.st_uid != os.geteuid():
            raise ValueError('Native session steps must exclude generated-code access')
        target.chmod(0o700)
    if (metadata.st_uid, metadata.st_gid) != (owner.st_uid, owner.st_gid):
        os.chown(target, owner.st_uid, owner.st_gid)
    return target


class SessionSandbox:
    def __init__(self, request, directory, execute=run_command):
        self.request, self.directory, self.execute = copy.deepcopy(request), Path(directory), execute
        self.lane = request.get('lane')
        if self.lane not in {'mechanics', 'electronics', 'firmware', 'application'}:
            raise ValueError('Native sessions require one supported engineering lane')
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise ValueError('Native workspace must not be a symlink')
        self.work = self.directory / self.lane
        self.work.mkdir(exist_ok=True)
        if self.work.is_symlink():
            raise ValueError('Native lane workspace must not be a symlink')
        supplied = request.get('sessionLimits', {})
        if not isinstance(supplied, dict) or set(supplied) - set(DEFAULT_LIMITS):
            raise ValueError('Unknown native session limits')
        self.limits = {}
        for name, default in DEFAULT_LIMITS.items():
            number = supplied.get(name, default)
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= HARD_LIMITS[name]:
                raise ValueError(f'Invalid native session limit: {name}')
            self.limits[name] = number
        identity = _sha(_json({'directory': str(self.directory.resolve()), 'jobId': request.get('jobId'),
                               'attemptId': request.get('attemptId')}))[:32]
        self.journal = self.directory.parent / ('.kleos-session-' + identity)
        if self.journal.is_symlink():
            raise ValueError('Native journal must not be a symlink')
        self.journal.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.journal, 0o700)
        if self.journal.stat().st_uid != os.geteuid():
            raise ValueError('Native journal is not owned by the supervisor')
        (self.journal / 'sources').mkdir(mode=0o700, exist_ok=True)
        self._started_clock = time.monotonic()
        fingerprint = _sha(_json(request))
        state_file = self.journal / 'state.json'
        if state_file.exists():
            self.state = json.loads(state_file.read_bytes())
            if self.state['requestHash'] != fingerprint:
                raise ValueError('Native session cannot resume with a changed execution contract')
            self._recover_pending()
            self._verify_sources()
        else:
            sources = {}
            for item in request.get('inputArtifacts', []):
                if item.get('lane') != self.lane:
                    continue
                name = item['name']
                _editable(name)
                if name in sources:
                    raise ValueError('Duplicate native session source path')
                raw = base64.b64decode(item['contentBase64'], validate=True)
                if len(raw) > MAX_FILE_BYTES or _sha(raw) != item['sha256']:
                    raise ValueError('Native session input hash or size is invalid')
                path = self.work / name
                _regular(path, self.work, False)
                if path.exists() and path.read_bytes() != raw:
                    raise ValueError('Existing native source differs from its pinned input')
                self._write(path, raw)
                self._blob(raw)
                sources[name] = _sha(raw)
                self._source_bounds(sources)
            self._source_bounds(sources)
            self.state = {'schemaVersion': 'native-session-v1', 'requestHash': fingerprint,
                          'startedAt': time.time(), 'sources': sources, 'actions': {}, 'pending': None,
                          'steps': 0, 'finished': False, 'latestBuild': None, 'buildSources': None}
            self._save()
        self._initial_elapsed = max(0., time.time() - self.state['startedAt'])

    @staticmethod
    def _write(path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.session-write-', dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def _save(self):
        self._write(self.journal / 'state.json', _json(self.state))

    def _blob(self, raw):
        path = self.journal / 'sources' / _sha(raw)
        if not path.exists():
            self._write(path, raw)
        return path

    def _source_bounds(self, sources):
        if len(sources) > MAX_SOURCE_FILES:
            raise ValueError('Native session source file ceiling exceeded')
        total = 0
        for digest in sources.values():
            path = self.journal / 'sources' / digest
            total += path.stat().st_size
        if total > self.limits['maxSourceBytes']:
            raise ValueError('Native session source byte ceiling exceeded')

    def _verify_sources(self):
        for name, digest in self.state['sources'].items():
            path = _regular(self.work / name, self.work)
            if path.stat().st_size > MAX_FILE_BYTES or _sha(path.read_bytes()) != digest:
                raise ValueError('Native source changed without an authorized patch')

    def _restore_sources(self):
        for name, digest in self.state['sources'].items():
            path = _regular(self.work / name, self.work, False)
            self._write(path, (self.journal / 'sources' / digest).read_bytes())

    def _recover_pending(self):
        pending = self.state.get('pending')
        if not pending:
            return
        if pending['action'] == 'patch':
            name, digest = pending['path'], pending['sha256']
            path = _regular(self.work / name, self.work, False)
            self._write(path, (self.journal / 'sources' / digest).read_bytes())
            self.state['sources'][name] = digest
            response = pending['response']
        else:
            self._restore_sources()
            if pending['action'] == 'build':
                self.state['latestBuild'], self.state['buildSources'] = None, None
            response = {'status': 'failed', 'result': {'error': 'interrupted_action', 'checks': [],
                        'message': 'The supervisor interrupted this action; sources were restored. Use a new action ID to retry.'}}
        self.state['actions'][pending['id']] = {'requestHash': pending['requestHash'], 'response': response}
        self.state['pending'] = None
        self._save()

    def _remaining(self):
        elapsed = max(time.monotonic() - self._started_clock + self._initial_elapsed,
                      time.time() - self.state['startedAt'])
        remaining = self.limits['maxSeconds'] - elapsed
        if not math.isfinite(remaining) or remaining <= 0:
            raise TimeoutError('Native session time ceiling exhausted')
        return remaining

    def _run(self, args, directory, timeout):
        ceiling = math.floor(min(timeout, self._remaining()))
        if ceiling < 1:
            raise TimeoutError('Insufficient native session time for another tool process')
        return self.execute(args, directory, ceiling)

    def apply(self, action):
        ident = action.get('id') if isinstance(action, dict) else None
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', ident):
            return {'status': 'failed', 'result': {'error': 'invalid_action_id', 'checks': []}}
        try:
            operation, arguments = action.get('action'), action.get('arguments', {})
            if set(action) - {'id', 'action', 'arguments'} or operation not in ACTIONS or not isinstance(arguments, dict):
                raise ValueError('Only fixed read, patch, build, analyze, render and finish actions are allowed')
            fingerprint = _sha(_json(action))
            previous = self.state['actions'].get(ident)
            if previous:
                if previous['requestHash'] != fingerprint:
                    return {'status': 'failed', 'result': {'error': 'action_id_conflict', 'checks': [],
                            'message': 'An action ID cannot be reused with different arguments'}}
                return copy.deepcopy(previous['response'])
            if self.state['finished']:
                raise ValueError('Native session is already finished')
            if self.state['steps'] >= self.limits['maxSteps']:
                raise ValueError('Native session step ceiling exhausted')
            self._remaining()
            self._verify_sources()
            self.state['steps'] += 1
            self.state['pending'] = {'id': ident, 'action': operation, 'requestHash': fingerprint}
            self._save()
            result = getattr(self, '_' + operation)(arguments)
            result['checks'] = []
            result.setdefault('sourceHashes', dict(self.state['sources']))
            result.setdefault('authority', 'diagnostic_only')
            response = {'status': 'succeeded', 'result': result}
        except (ValueError, TypeError, KeyError, OSError, RuntimeError, TimeoutError, UnsupportedTool, zipfile.BadZipFile) as exc:
            response = {'status': 'failed', 'result': {'error': type(exc).__name__, 'message': str(exc)[:4000],
                         'logs': getattr(exc, 'logs', '')[-100000:], 'checks': []}}
        if len(_json(response)) > MAX_STEP_BYTES:
            response = {'status': 'failed', 'result': {'error': 'step_result_limit', 'message': 'Native step response exceeds 2 MiB', 'checks': []}}
        if not self.state.get('pending') or self.state['pending']['id'] != ident:
            return response
        self.state['actions'][ident] = {'requestHash': _sha(_json(action)), 'response': response}
        self.state['pending'] = None
        self._save()
        return copy.deepcopy(response)

    def _read(self, arguments):
        if set(arguments) - {'path', 'offset', 'maxBytes'}:
            raise ValueError('Unsupported read arguments')
        name = str(_relative(arguments['path']))
        if any(part in {'.home', '.tmp', '.git', '.dart_tool', '.cache'} for part in PurePosixPath(name).parts):
            raise ValueError('Private tool state cannot be read')
        if name.startswith('native/'):
            output = self._output(name)
            raw = base64.b64decode(output.content) if output.encoding == 'base64' else output.content.encode()
        else:
            if name not in self.state['sources']:
                raise ValueError('Only tracked lane source files and recorded build outputs can be read')
            raw = _regular(self.work / name, self.work).read_bytes()
        offset, ceiling = arguments.get('offset', 0), arguments.get('maxBytes', min(65536, self.limits['maxReadBytes']))
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (offset, ceiling)) or not (
                0 <= offset <= len(raw) and 1 <= ceiling <= self.limits['maxReadBytes']):
            raise ValueError('Invalid bounded read range')
        try:
            content = raw[offset:offset + ceiling].decode('utf8')
        except UnicodeDecodeError:
            return {'path': name, 'size': len(raw), 'sha256': _sha(raw), 'binary': True,
                    'content': '', 'message': 'Binary outputs are described by metadata; use render for STEP views'}
        return {'path': name, 'size': len(raw), 'sha256': _sha(raw), 'content': content,
                'offset': offset, 'truncated': offset + ceiling < len(raw)}

    def _patch(self, arguments):
        if set(arguments) != {'path', 'expectedSha256', 'content'}:
            raise ValueError('Patch requires path, expectedSha256 and complete UTF-8 content')
        name = str(_editable(arguments['path']))
        content, expected = arguments['content'], arguments['expectedSha256']
        if not isinstance(content, str) or '\0' in content:
            raise ValueError('Patch must contain plain UTF-8 text')
        raw = content.encode('utf8')
        if len(raw) > self.limits['maxPatchBytes']:
            raise ValueError('Patch byte ceiling exceeded')
        path = _regular(self.work / name, self.work, False)
        current = self.state['sources'].get(name)
        if expected != current or current is None and path.exists():
            raise ValueError('Optimistic patch hash conflict; read the current source before patching')
        if expected is not None and not isinstance(expected, str):
            raise ValueError('Expected patch hash must be SHA-256 or null for creation')
        self._blob(raw)
        updated = {**self.state['sources'], name: _sha(raw)}
        self._source_bounds(updated)
        result = {'path': name, 'sha256': _sha(raw), 'previousSha256': current, 'size': len(raw), 'checks': []}
        self.state['pending'].update(path=name, sha256=_sha(raw), response={'status': 'succeeded', 'result': result})
        self._save()
        self._write(path, raw)
        self.state['sources'] = updated
        return result

    def _clean_build(self):
        names = ['outputs', 'build', '.dart_tool', '.home', '.tmp', 'companion-web.zip', 'companion-build-manifest.json',
                 '.kleos-tool-request.json', 'kleos-build.json']
        if self.lane == 'application':
            names.extend(['assets/kleos_fonts', 'android/local.properties', '.flutter-plugins-dependencies',
                '.packages', 'android/.gradle', 'android/app/src/main/java/io/flutter/plugins/GeneratedPluginRegistrant.java',
                'ios/.symlinks', 'ios/Flutter/ephemeral', 'ios/Flutter/Generated.xcconfig',
                'ios/Flutter/flutter_export_environment.sh', 'ios/Runner/GeneratedPluginRegistrant.h',
                'ios/Runner/GeneratedPluginRegistrant.m'])
        for name in names:
            path = self.work / name
            cursor = self.work
            for part in PurePosixPath(name).parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ValueError('Derived build path is a symlink')
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()

    def _build(self, arguments):
        if arguments:
            raise ValueError('Build uses the pinned lane/tool contract and accepts no shell arguments')
        self._clean_build()
        before = dict(self.state['sources'])
        self.state['latestBuild'], self.state['buildSources'] = None, None
        request = {**self.request, 'purpose': 'build', 'timeoutSeconds': self._remaining(), 'inputArtifacts': [
            {'id': name, 'name': name, 'lane': self.lane, 'sha256': digest,
             'contentBase64': base64.b64encode((self.journal / 'sources' / digest).read_bytes()).decode()}
            for name, digest in before.items()]}
        try:
            result = build_lane(request, self.directory, self._run)
            prepared = self._prepared_sources(result)
            for name, digest in before.items():
                path = _regular(self.work / name, self.work)
                actual = _sha(path.read_bytes())
                if actual != digest and prepared.get(name) != actual:
                    raise ValueError('Build changed a pinned source outside an authorized patch')
            self.state['sources'].update(prepared)
            self._source_bounds(self.state['sources'])
        except UnsupportedTool as exc:
            result = JobResult(status='unsupported', limitations=[str(exc)])
            self._restore_sources()
        except (NativeBuildError, NativeTimeoutError, RuntimeError, ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            kind = 'resource' if isinstance(exc, NativeResourceError) else 'deadline' if isinstance(exc, (NativeTimeoutError, TimeoutError)) else 'engineering'
            result = JobResult(status='failed', logs=getattr(exc, 'logs', '')[-100000:], limitations=[str(exc)[:4000]],
                               measurements={'failureKind': kind})
            self.state['sources'] = before
            self._restore_sources()
        result.checks = []
        self._write(self.journal / 'latest-build.json', result.model_dump_json(by_alias=True).encode())
        self.state['latestBuild'] = 'latest-build.json'
        self.state['buildSources'] = dict(self.state['sources'])
        return {'status': result.status, 'logs': result.logs, 'limitations': result.limitations,
                'measurements': result.measurements, 'files': self._metadata(result.files),
                'sourceHashes': dict(self.state['sources']), 'candidate': True}

    def _prepared_sources(self, result):
        if self.lane != 'application' or result.measurements.get('preparation') not in {'offline-companion-scaffold-v2', 'offline-companion-scaffold-v3'}:
            return {}
        bundle = next((item for item in result.files if item.path == 'native/companion-source.zip'), None)
        if not bundle or bundle.encoding != 'base64':
            raise ValueError('Prepared application source archive is missing')
        raw = base64.b64decode(bundle.content, validate=True)
        if _sha(raw) != result.measurements.get('preparedSourceSha256'):
            raise ValueError('Prepared application source archive hash differs')
        prepared, seen, total = {}, set(), 0
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if len(archive.infolist()) > MAX_SOURCE_FILES:
                raise ValueError('Prepared application source file ceiling exceeded')
            for info in archive.infolist():
                name = str(_relative(info.filename))
                if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError('Prepared application source must contain regular files')
                if info.file_size > MAX_FILE_BYTES or name in seen:
                    raise ValueError('Prepared application source entry is invalid')
                seen.add(name)
                if name != 'kleos-build.json' and not name.startswith(PREPARED_PREFIX):
                    _editable(name)
                total += info.file_size
                if total > self.limits['maxSourceBytes']:
                    raise ValueError('Prepared application source archive exceeds its ceiling')
                content = archive.read(info)
                path = _regular(self.work / name, self.work)
                if _sha(path.read_bytes()) != _sha(content):
                    raise ValueError('Prepared application source changed during its build')
                # Preparation-only fonts/metadata remain in the complete ZIP,
                # not in the editable/reseed source closure for the next attempt.
                if name != 'kleos-build.json' and not name.startswith(PREPARED_PREFIX):
                    self._blob(content)
                    prepared[name] = _sha(content)
        return prepared

    def _latest(self):
        if not self.state['latestBuild'] or self.state['buildSources'] != self.state['sources']:
            raise ValueError('Build the current source before observing or finishing outputs')
        return JobResult.model_validate_json((self.journal / self.state['latestBuild']).read_bytes())

    def _output(self, name):
        result = self._latest()
        output = next((item for item in result.files if item.path == name), None)
        if output is None:
            raise ValueError('Requested output does not belong to the latest source-bound build')
        return output

    @staticmethod
    def _metadata(files):
        metadata = []
        for item in files:
            raw = base64.b64decode(item.content) if item.encoding == 'base64' else item.content.encode()
            metadata.append({'path': item.path, 'sha256': _sha(raw), 'size': len(raw), 'mediaType': item.media_type})
        return metadata

    def _analyze(self, arguments):
        if arguments:
            raise ValueError('Analyze accepts no commands or arbitrary flags')
        if self.lane == 'mechanics':
            from .native_runner import MECHANICAL_INSPECT
            output = self._output('native/outputs/model.step')
            with tempfile.TemporaryDirectory(prefix='.session-analysis-', dir=self.directory) as temporary:
                work = Path(temporary)
                step = work / 'assembly.step'
                step.write_bytes(base64.b64decode(output.content))
                code, logs = self._run(['python', '-I', '-c', MECHANICAL_INSPECT, str(step),
                    json.dumps(self.request.get('interfaces', {}).get('mechanical', {})),
                    json.dumps(self.request.get('requirements', {}))], work, 120)
                measured = json.loads(logs.strip().splitlines()[-1]) if not code else None
            return {'status': 'succeeded' if not code else 'failed', 'logs': logs, 'measurements': measured,
                    'candidate': True, 'authority': 'diagnostic_only'}
        if self.lane == 'firmware':
            output = self._output('native/build/zephyr/zephyr.elf')
            with tempfile.TemporaryDirectory(prefix='.session-analysis-', dir=self.directory) as temporary:
                work = Path(temporary)
                elf = work / 'firmware.elf'
                elf.write_bytes(base64.b64decode(output.content))
                code, logs = self._run(['readelf', '-h', str(elf)], work, 120)
            return {'status': 'succeeded' if not code else 'failed', 'logs': logs, 'candidate': True,
                    'authority': 'diagnostic_only'}
        if self.lane == 'electronics':
            result = self._latest()
            boards = [item for item in result.files if item.path.endswith('.kicad_pcb')]
            if len(boards) != 1:
                return {'status': 'unsupported', 'reason': 'One actual PCB output is required for fixed analysis', 'candidate': True}
            with tempfile.TemporaryDirectory(prefix='.session-analysis-', dir=self.directory) as temporary:
                from .design_validation import PCB_LAYOUT_SCRIPT
                work = Path(temporary)
                board = work / 'circuit.kicad_pcb'
                board.write_bytes(base64.b64decode(boards[0].content))
                code, logs = self._run(['/usr/bin/python3', '-I', '-c', PCB_LAYOUT_SCRIPT, str(board)], work, 120)
                measured = json.loads(logs.strip().splitlines()[-1]) if not code else None
            return {'status': 'succeeded' if not code else 'failed', 'logs': logs, 'measurements': measured,
                    'candidate': True, 'authority': 'diagnostic_only'}
        self._latest()
        code, logs = self._run(['flutter', 'analyze', '--no-pub'], self.work, 120)
        self._verify_sources()
        return {'status': 'succeeded' if not code else 'failed', 'logs': logs, 'candidate': True, 'authority': 'diagnostic_only'}

    def _render(self, arguments):
        if set(arguments) - {'path', 'views'}:
            raise ValueError('Render accepts only a recorded STEP path and fixed views')
        if self.lane != 'mechanics':
            return {'status': 'unsupported', 'reason': 'Actual STEP orthographic rendering is available for mechanics', 'images': []}
        from .cad_render import MAX_IMAGE_BYTES, VIEWS
        name, views = arguments.get('path', 'native/outputs/model.step'), arguments.get('views', ['front', 'top', 'isometric'])
        if (not isinstance(name, str) or not name.endswith('.step') or not isinstance(views, list)
                or not 1 <= len(views) <= 3 or any(not isinstance(view, str) or view not in VIEWS for view in views)
                or len(set(views)) != len(views)):
            raise ValueError('Render requires a STEP output and up to three fixed views')
        output = self._output(name)
        raw = base64.b64decode(output.content, validate=True)
        with tempfile.TemporaryDirectory(prefix='.session-render-', dir=self.directory) as temporary:
            work = Path(temporary)
            step, target = work / 'assembly.step', work / 'images'
            step.write_bytes(raw)
            code, logs = self._run(['python', '-I', '-m', 'kleos.cad_render', str(step), str(target), json.dumps(views)], work, 120)
            if code:
                raise NativeBuildError('Actual STEP rendering failed', logs)
            metadata = json.loads(logs.strip().splitlines()[-1])
            if metadata.get('stepSha256') != _sha(raw) or len(metadata.get('images', [])) != len(views):
                raise ValueError('CAD render does not match its pinned STEP/views')
            images = []
            for view, item in zip(views, metadata['images']):
                path = _regular(target / f'{view}.png', work)
                if path.stat().st_size > MAX_IMAGE_BYTES:
                    raise ValueError('CAD view exceeds 512 KiB')
                image = path.read_bytes()
                if not image.startswith(b'\x89PNG\r\n\x1a\n') or _sha(image) != item.get('sha256'):
                    raise ValueError('CAD view image/hash is invalid')
                images.append({'name': f'{view}.png', 'mediaType': 'image/png', 'sha256': _sha(image),
                               'contentBase64': base64.b64encode(image).decode(), 'stepSha256': _sha(raw)})
        return {'images': images, 'stepSha256': _sha(raw), 'sourceHashes': dict(self.state['sources']),
                'candidate': True, 'authority': 'diagnostic_only'}

    def _finish(self, arguments):
        if arguments:
            raise ValueError('Finish accepts no publication or validation arguments')
        result = self._latest()
        self.state['finished'] = True
        return {'status': result.status, 'candidate': True, 'sourceHashes': dict(self.state['sources']),
                'outputFiles': self._metadata(result.files), 'authority': 'candidate_only'}

    def finish_result(self):
        """Supervisor-only transport result; no model-visible large artifact body."""
        try:
            self._verify_sources()
        except (ValueError, OSError) as exc:
            return JobResult(status='failed', checks=[], limitations=[str(exc)[:4000]])
        if not self.state['finished']:
            return JobResult(status='failed', checks=[], limitations=['Native session did not complete a finish action'])
        result = self._latest()
        def media_type(name):
            if name.endswith('.json'):
                return 'application/json'
            if Path(name).suffix.lower() in {'.dart', '.py', '.c', '.h', '.yaml', '.yml', '.xml', '.plist', '.kts',
                    '.gradle', '.conf', '.lock', '.md', '.txt', '.xcconfig', '.pbxproj', '.properties'}:
                return 'text/plain'
            return 'application/octet-stream'
        files = [GeneratedFile(path=name, content=base64.b64encode((self.journal / 'sources' / digest).read_bytes()).decode(),
                               media_type=media_type(name), encoding='base64')
                 for name, digest in sorted(self.state['sources'].items())]
        files.extend(result.files)
        if len(files) > 256 or sum(len(base64.b64decode(item.content)) if item.encoding == 'base64'
                                  else len(item.content.encode()) for item in files) > MAX_TOTAL_BYTES:
            return JobResult(status='failed', checks=[], limitations=['Session source/output closure exceeds result limits'])
        return JobResult(status=result.status, files=files, checks=[], logs=result.logs,
            measurements={**result.measurements, 'nativeSession': {'schemaVersion': 'native-session-v1', 'candidate': True,
                'jobId': self.request.get('jobId'), 'attemptId': self.request.get('attemptId'),
                'requestHash': self.state['requestHash'], 'steps': self.state['steps'],
                'sourceHashes': dict(self.state['sources']), 'limits': self.limits}},
            limitations=[*result.limitations, 'Session actions are diagnostic and candidate-only; independent release validation remains required'])
