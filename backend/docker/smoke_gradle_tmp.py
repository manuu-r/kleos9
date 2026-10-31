"""Tiny offline Gradle/JNI qualification with generated UID and read-only root."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from kleos.native_runner import WORKER_UID, configure_resources, unprivileged_limits

configure_resources({'lane': 'application', 'purpose': 'build'})
gradle = list(Path('/opt/gradle-cache/wrapper/dists').glob('gradle-8.12-bin/*/gradle-8.12/bin/gradle'))
assert len(gradle) == 1
results = []
for explicit in (False, True):
    with tempfile.TemporaryDirectory(prefix='gradle-temp-probe-') as temporary:
        work = Path(temporary)
        work.chmod(0o755)
        (work / 'settings.gradle').write_text("rootProject.name='qualified-temp-probe'\n")
        (work / 'build.gradle').write_text('')
        for name in ('.home', '.tmp', '.gradle'):
            (work / name).mkdir()
        for path in [work, *work.rglob('*')]:
            os.chown(path, WORKER_UID, WORKER_UID)
        options = '-XX:ActiveProcessorCount=2 -Xmx512m -Duser.home=' + str(work / '.home')
        if explicit:
            options += ' -Djava.io.tmpdir=' + str(work / '.tmp')
        environment = {'PATH': '/opt/java17/bin:/usr/local/bin:/usr/bin:/bin', 'JAVA_HOME': '/opt/java17',
            'JAVA_TOOL_OPTIONS': options, 'HOME': str(work / '.home'), 'TMPDIR': str(work / '.tmp'),
            'GRADLE_USER_HOME': str(work / '.gradle'), 'LANG': 'C.UTF-8'}
        process = subprocess.run([str(gradle[0]), 'help', '--offline', '--no-daemon', '--stacktrace'],
            cwd=work, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            preexec_fn=unprivileged_limits(60), timeout=75, close_fds=True)
        logs = process.stdout.decode(errors='replace')
        results.append({'privateJavaTmpExplicit': explicit, 'returnCode': process.returncode,
            'buildSuccessful': 'BUILD SUCCESSFUL' in logs, 'logs': logs[-16000:]})
print(json.dumps({'tmpMode': oct(Path('/tmp').stat().st_mode & 0o7777), 'cases': results}), flush=True)
