"""Check Docker's actual anonymous-volume API/cleanup, without engineering or AI."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from kleos.config import Settings


def main():
    settings = Settings()
    if settings.environment not in {'development', 'test'}:
        raise RuntimeError('This probe is local-development only')
    fixture = str(uuid4())
    name = 'kleos-scratch-proof-' + fixture
    identity = None
    backend = Path(__file__).resolve().parents[1]
    with httpx.Client(transport=httpx.HTTPTransport(uds=settings.docker_socket),
                      base_url='http://docker/v1.45', trust_env=False, timeout=30) as client:
        image = client.get('/images/kleos-control:development/json')
        image.raise_for_status()
        shared = settings.docker_jobs_volume
        assert client.get('/volumes/' + shared).status_code == 200
        specification = {'Image': image.json()['Id'], 'User': '0',
            'Entrypoint': ['python', '-I', '-c',
                'from pathlib import Path; p=Path("/tmp/probe"); p.write_text("private scratch"); assert p.read_text()=="private scratch"'],
            'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Memory': 256 * 1024**2,
                'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges:true'],
                'Mounts': [{'Type': 'volume', 'Target': '/tmp', 'VolumeOptions': {
                    'NoCopy': True, 'Labels': {'kleos.job': fixture, 'kleos.role': 'native-scratch'}}}]}}
        try:
            created = client.post('/containers/create', params={'name': name}, json=specification)
            created.raise_for_status()
            identity = created.json()['Id']
            client.post(f'/containers/{identity}/start').raise_for_status()
            completed = client.post(f'/containers/{identity}/wait', params={'condition': 'not-running'})
            completed.raise_for_status()
            assert completed.json()['StatusCode'] == 0
            inspected = client.get(f'/containers/{identity}/json').json()
            mount = next(m for m in inspected['Mounts'] if m['Destination'] == '/tmp')
            assert mount['Type'] == 'volume' and mount['Name'] != shared
            volume = client.get('/volumes/' + mount['Name']).json()
            assert volume['Labels']['kleos.job'] == fixture
            deleted = client.delete(f'/containers/{identity}', params={'force': 'false', 'v': 'true'})
            deleted.raise_for_status()
            assert client.get('/volumes/' + mount['Name']).status_code == 404
            assert client.get('/volumes/' + shared).status_code == 200
        finally:
            if identity:
                client.delete(f'/containers/{identity}', params={'force': 'true', 'v': 'true'})
    report = {'status': 'passed', 'checkedAt': datetime.now(UTC).isoformat(), 'modelCalls': 0,
        'nativeBuilds': 0, 'imageId': image.json()['Id'], 'actualDockerApi': 'v1.45',
        'privateDiskScratchWritable': True, 'anonymousScratchRemoved': True, 'sharedControlVolumePreserved': True,
        'network': 'none', 'readOnlyRoot': True, 'scratchHardByteQuota': None,
        'executionSourceSha256': hashlib.sha256((backend / 'kleos/execution.py').read_bytes()).hexdigest()}
    target = backend / 'verification/native-scratch-runtime.json'
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'report': str(target), 'modelCalls': 0}))


if __name__ == '__main__':
    main()
