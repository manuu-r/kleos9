"""Opt-in isolated Compose smoke; no models, native jobs, cloud calls or user volumes."""
import base64
import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]


def main():
    stamp = secrets.token_hex(5)
    project = 'kleos2-packaging-' + stamp
    work = ROOT / '.data/qualification' / project
    work.mkdir(parents=True)
    password, session_secret, setup = (secrets.token_hex(32) for _ in range(3))
    private = work / 'runtime.env'
    contents = {
        'KLEOS2_POSTGRES_PASSWORD': password,
        'KLEOS2_DATABASE_URL': f'postgresql+psycopg://kleos:{password}@postgres:5432/kleos2_local',
        'KLEOS2_SESSION_SECRET': session_secret, 'KLEOS2_SETUP_TOKEN': setup,
        'KLEOS2_CONTROL_IMAGE': 'kleos2-control:local', 'KLEOS2_LOCAL_API_PORT': '18870',
        'KLEOS2_LOCAL_PREVIEW_PORT': '18874', 'KLEOS2_API_ORIGIN': 'http://127.0.0.1:18870',
        'KLEOS2_PREVIEW_ORIGIN': 'http://127.0.0.1:18874', 'KLEOS2_FRONTEND_ORIGIN': 'http://127.0.0.1:18880',
        'KLEOS2_COMPANION_PREVIEW_ENABLED': 'true', 'KLEOS2_META_API_KEY': '', 'KLEOS_META_API_KEY': '',
        'KLEOS2_MODAL_API_KEY': '', 'KLEOS_MODAL_LLM_KEY': '', 'KLEOS2_MODAL_BASE_URL': '', 'KLEOS_MODAL_LLM_URL': '',
    }
    descriptor = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(''.join(f'{key}={value}\n' for key, value in contents.items()))
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('KLEOS_', 'KLEOS2_'))}
    command = ['docker', 'compose', '--env-file', str(private), '-f', str(ROOT / 'compose.yaml'), '--project-name', project]
    report = {'qualified': False, 'scope': 'Local control-plane packaging snapshot only', 'project': project,
        'providerCalls': 0, 'nativeJobs': 0, 'workerStarted': False, 'steps': [], 'volumesRetained': True}

    def run(args, *, timeout=90):
        result = subprocess.run([*command, *args], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=timeout)
        report['steps'].append({'operation': args, 'exitCode': result.returncode})
        if result.returncode:
            raise RuntimeError('Compose packaging step failed; inspect the isolated fixture containers')
        return result.stdout

    def ready(client):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                response = client.get('/api/v1/ready')
                if response.status_code == 200:
                    return response.json()
            except httpx.TransportError:
                pass
            time.sleep(.2)
        raise RuntimeError('Packaged API never became ready')

    try:
        run(['config', '--quiet'])
        run(['up', '-d', '--wait', 'postgres'])
        run(['--profile', 'setup', 'run', '--rm', 'migrate'])
        run(['up', '-d', 'api'])
        with httpx.Client(base_url='http://127.0.0.1:18870', timeout=5, trust_env=False) as client:
            assert ready(client)['apiAvailable'] is True
            setup_response = client.post('/api/v1/auth/setup', json={'setupToken': setup,
                'email': 'packaging@example.test', 'password': secrets.token_hex(20), 'organization': 'Packaging fixture',
                'name': 'Fixture', 'native': True})
            assert setup_response.status_code == 200
            client.headers['Authorization'] = 'Bearer ' + setup_response.json()['token']
            created = client.post('/api/v1/commands', headers={'Idempotency-Key': 'package-create'},
                json={'type': 'project.create', 'payload': {'brief': 'Packaging persistence fixture', 'autoStart': False}})
            assert created.status_code == 202
            ident = created.json()['projectId']
            content = b'Authoritative artifact survives container recreation.'
            uploaded = client.post('/api/v1/projects/' + ident + '/artifacts', headers={'Idempotency-Key': 'package-upload'},
                json={'expectedEngineeringRevision': 1, 'sha256': hashlib.sha256(content).hexdigest(),
                    'file': {'path': 'context.txt', 'mediaType': 'text/plain', 'encoding': 'base64',
                        'content': base64.b64encode(content).decode()}})
            assert uploaded.status_code == 201
            artifact = uploaded.json()['artifact']['id']
            run(['up', '-d', '--no-deps', '--force-recreate', 'api'])
            ready(client)
            assert client.get('/api/v1/artifacts/' + artifact + '/content').content == content
            assert client.get('/api/v1/projects/' + ident + '/workspace').status_code == 200
            report['databaseSessionAndArtifactSurvivedApiRecreation'] = True
        run(['--profile', 'preview', 'up', '-d', 'preview'])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                response = httpx.get('http://127.0.0.1:18874/api/v1/health', timeout=3, trust_env=False)
                if response.status_code == 404:
                    break
            except httpx.TransportError:
                pass
            time.sleep(.2)
        else:
            raise RuntimeError('Separate packaged preview process did not start')
        report['previewHasNoApiRoute'] = True
        report['qualified'] = True
    finally:
        run(['--profile', 'preview', '--profile', 'setup', 'down', '--remove-orphans'])
        (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'qualified': report['qualified'], 'report': str(work / 'report.json'),
            'volumesRetained': True, 'providerCalls': 0, 'workerStarted': False}))


if __name__ == '__main__':
    main()
