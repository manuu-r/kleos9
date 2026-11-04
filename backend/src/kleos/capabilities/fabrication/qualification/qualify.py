"""Explicit local, offline qualification of the exact installed rewritten slicer image."""
import argparse
import asyncio
import base64
import json
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from kleos.capabilities.fabrication.slicing import (
    BUNDLES,
    DockerSlicingDriver,
    SlicingSettings,
    digest,
    validate_package,
)
from kleos.execution.docker import DockerClient
from kleos.execution.security import BASELINE_SHA256


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    points = [(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0), (0, 0, 20), (20, 0, 20), (20, 20, 20), (0, 20, 20)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    source = ('\n'.join('v %s %s %s' % p for p in points) + '\n' +
              '\n'.join('f %s %s %s' % tuple(i + 1 for i in f) for f in faces)).encode()
    docker = DockerClient(socket=args.socket)
    report = {'image': args.image, 'sourceOverride': False, 'sourceSha256': digest(source),
              'runnerSha256': digest(Path(__file__).parents[1].joinpath('native_slicer.py').read_bytes()),
              'securityBaselineSha256': BASELINE_SHA256, 'environment': 'local-docker-desktop-amd64',
              'fixture': '20mm closed cube triangulated OBJ', 'physicalAcceptance': False, 'profiles': {}}
    with tempfile.TemporaryDirectory(prefix='kleos-slicer-qualification-') as directory:
        settings = SlicingSettings(args.image, Path(directory), tuple(BUNDLES), docker_desktop=True)
        driver = DockerSlicingDriver(docker, settings)
        for profile in BUNDLES:
            job = uuid4().hex
            start = time.monotonic()
            try:
                await driver.start(job, source, 'obj', {**settings.profile(profile), 'id': profile})
                result = None
                while time.monotonic() - start < 650:
                    result = await driver.result(job)
                    if result is not None:
                        break
                    await asyncio.sleep(2)
                if result is None or result.get('status') != 'ready':
                    raise ValueError('Isolated slicer did not produce a ready result: ' + str(result))
                package = base64.b64decode(result.pop('package'), validate=True)
                checks = validate_package(package)
                report['profiles'][profile] = {'status': 'PASS', 'bundleSha256': BUNDLES[profile][2],
                    'packageSha256': digest(package), 'checks': checks, 'measurements': result['measurements'],
                    'elapsedSeconds': round(time.monotonic() - start, 2)}
                print(profile + ': PASS', flush=True)
            except Exception as error:
                report['profiles'][profile] = {'status': 'FAIL', 'error': str(error),
                    'elapsedSeconds': round(time.monotonic() - start, 2)}
                print(profile + ': FAIL ' + str(error), flush=True)
                logs = await docker.client.get('/containers/kleos-slicer-' + job + '/logs', params={'stdout': 'true', 'stderr': 'true', 'tail': '20'})
                print(logs.content[:8192], flush=True)
            finally:
                await driver.cleanup(job)
        report['rejections'] = {}
        for scenario, content, changes in [
                ('open-mesh', source.rsplit(b'\n', 1)[0], {}),
                ('modified-preset', source, {'bundleSha256': '0' * 64})]:
            job = uuid4().hex
            try:
                profile = {**settings.profile('a1-mini-pla-04'), 'id': 'a1-mini-pla-04', **changes}
                await driver.start(job, content, 'obj', profile)
                result = None
                for _ in range(30):
                    result = await driver.result(job)
                    if result is not None:
                        break
                    await asyncio.sleep(1)
                rejected = result is not None and result.get('status') == 'failed' and 'package' not in result
                report['rejections'][scenario] = 'PASS' if rejected else 'FAIL'
                print(scenario + ': ' + report['rejections'][scenario], flush=True)
            finally:
                await driver.cleanup(job)
        await docker.close()
    Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
    if (any(row['status'] != 'PASS' for row in report['profiles'].values())
            or any(value != 'PASS' for value in report['rejections'].values())):
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
