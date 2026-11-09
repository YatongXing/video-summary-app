"""Test real Web/API and a mocked Qwen transport in a disposable S3-12 project."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
WORKER = """
import time
from unittest.mock import patch

from app.qwen_adapter import QwenModelAdapter
from app.worker import main
from test_qwen_adapter import MockCloudStore, MockTransport


class SyntheticTransport(MockTransport):
    calls = 0

    def request(self, *args, **kwargs):
        super().request(*args, **kwargs)
        SyntheticTransport.calls += 1
        self.number = SyntheticTransport.calls
        print(f'S312_MOCK_VALIDATED {self.number}', flush=True)

    def getresponse(self):
        if self.number == 2:
            time.sleep(10)
        return super().getresponse()


with patch('app.qwen_adapter.http.client.HTTPSConnection', SyntheticTransport):
    import os
    if os.getenv('S3_11_FIXTURE_CLOUD'):
        QwenModelAdapter.max_inline_video_bytes = 100
        with patch('app.qwen_cloud.CloudVideoStore.from_environment', return_value=MockCloudStore()):
            raise SystemExit(main())
    raise SystemExit(main())
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker-context', default='desktop-linux')
    parser.add_argument('--api-image', default='video-summary-app-s312-api:local')
    parser.add_argument('--worker-image', default='video-summary-app-s312-worker:local')
    parser.add_argument('--web-image', default='video-summary-app-s312-web:local')
    parser.add_argument('--cloud', action='store_true', help='Exercise the OSS branch with a mock store')
    args = parser.parse_args()
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(('WP07_', 'VSA_S311_', 'VSA_S312_', 'COMPOSE_', 'DOCKER_'))}
    environment['COMPOSE_DISABLE_ENV_FILE'] = '1'
    docker = ['docker', '--context', args.docker_context]
    project = 'vsa-s312-e2e-' + uuid4().hex[:10]
    fixture_image = 'vsa-s312-fixture:' + project.removeprefix('vsa-s312-e2e-')

    def run(command, *, stdin=None, timeout=120, binary=False):
        result = subprocess.run(docker + command, input=stdin, cwd=ROOT, env=environment,
                                encoding=None if binary else 'utf-8', errors=None if binary else 'replace',
                                capture_output=True, timeout=timeout)
        if result.returncode:
            error = result.stderr.decode('utf-8', errors='replace') if binary else result.stderr
            raise RuntimeError(f'Docker {command[0]} returned {result.returncode}: {error[-1800:]}')
        return result.stdout if binary else result.stdout.strip()

    ids = {name: run(['image', 'inspect', '--format', '{{.Id}}', tag])
           for name, tag in (('api', args.api_image), ('worker', args.worker_image), ('web', args.web_image))}
    video = run(['run', '--rm', '--network', 'none', '--pull', 'never', '--entrypoint', 'ffmpeg', args.worker_image,
                 '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:r=10',
                 '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '8', '-c:v', 'libx264',
                 '-threads:v', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+frag_keyframe+empty_moov',
                 '-f', 'mp4', 'pipe:1'], binary=True)

    def resources(kind):
        command = {'container': ['ps', '-aq'], 'volume': ['volume', 'ls', '-q'],
                   'network': ['network', 'ls', '-q']}[kind]
        return run(command + ['--filter', f'label=com.docker.compose.project={project}']).splitlines()

    def validate_owned():
        for kind in ('container', 'volume', 'network'):
            for name in resources(kind):
                label = ('{{index .Config.Labels "vsa.synthetic_only"}}' if kind == 'container'
                         else '{{index .Labels "vsa.synthetic_only"}}')
                assert run([kind, 'inspect', '--format', label, name]) == 'true', (kind, name)

    def sql(query):
        return run(['exec', project+'-db-1', 'psql', '-X', '-U', 'vsa_init', '-d', 's312_test',
                    '-At', '-v', 'ON_ERROR_STOP=1', '-c', query])

    frozen = json.loads((ROOT/'docs/baseline/cr-wp07a-fingerprints.json').read_text(encoding='utf-8'))
    approved = {
        Path(path).name: digest
        for path, digest in frozen['protected_approved_frontend_and_sql_sha256'].items()
        if path.startswith('backend/app/migrations/')
    }
    migrations = sorted((ROOT/'backend/app/migrations').glob('[0-9][0-9][0-9]_*.sql'))
    assert [path.name[:3] for path in migrations] == [f'{number:03}' for number in range(1, 12)]
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in migrations} == approved

    assert not any(resources(kind) for kind in ('container', 'volume', 'network'))
    with tempfile.TemporaryDirectory(prefix='vsa-s312-e2e-') as temporary:
        tmp = Path(temporary)
        sample = tmp/'sample.mp4'
        sample.write_bytes(video)
        fixtures = tmp/'fixtures'
        fixtures.mkdir()
        shutil.copyfile(ROOT/'backend/tests/test_qwen_adapter.py', fixtures/'test_qwen_adapter.py')
        (fixtures/'worker.py').write_text(WORKER, encoding='utf-8')
        run(['build', '--pull=false', '--network', 'none', '-t', fixture_image, '-f', '-', str(fixtures)],
            stdin=f'FROM {args.worker_image}\nCOPY test_qwen_adapter.py worker.py /srv/\n')
        try:
            unit = run(['run', '--rm', '--network', 'none', '--pull', 'never', fixture_image,
                        'python', '-m', 'unittest', '-q', 'test_qwen_adapter'])
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            config = {
                'WP07_PROJECT_NAME': project, 'WP07_SYNTHETIC_ONLY': 'true', 'WP07_DB': 's312_test',
                'WP07_HTTP_PORT': str(port), 'WP07_APP_IMAGE': args.api_image, 'WP07_WEB_IMAGE': args.web_image,
                'WP07_INIT_PASSWORD': uuid4().hex, 'WP07_MAINT_PASSWORD': uuid4().hex,
                'WP07_RUNTIME_PASSWORD': uuid4().hex, 'WP07_OBJECT_ACCESS_KEY': 'fixture'+uuid4().hex[:12],
                'WP07_OBJECT_SECRET_KEY': uuid4().hex,
            }
            (tmp/'test.env').write_text(''.join(f'{k}={v}\n' for k,v in config.items()), encoding='ascii')
            (tmp/'test.yaml').write_text(f"""services:
  worker:
    image: {fixture_image}
    command: ["python", "/srv/worker.py"]
    environment:
      WORKER_PROCESSOR_MODE: qwen
      MODEL_CALLS_ENABLED: "true"
      DASHSCOPE_API_KEY: sk-ws-synthetic-fixture
      S3_11_FIXTURE_SHA256: {hashlib.sha256(video).hexdigest()}
      S3_11_FIXTURE_CLOUD: "{'1' if args.cloud else ''}"
  web:
    networks: !override [private, web_ingress]
networks:
  private:
    internal: true
  web_ingress:
    labels:
      vsa.synthetic_only: "true"
""", encoding='ascii')
            compose = ['compose', '--project-name', project, '--env-file', str(tmp/'test.env'),
                       '-f', str(ROOT/'deploy/compose.wp07.yaml'), '-f', str(tmp/'test.yaml')]
            try:
                run(compose+['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '60', 'db', 'object-store'])
                assert run(['network', 'inspect', '--format', '{{.Internal}}', project+'_private']) == 'true'
                for service in ('volume-init', 'initialize'):
                    run(compose+['--profile', 'initialize', 'run', '--rm', '--no-deps', '--pull', 'never', service])
                ledger = sql("SELECT version, sha256 FROM schema_migrations ORDER BY version")
                assert dict(row.split('|', 1) for row in ledger.splitlines()) == approved
                assert sql("""SELECT NOT (
                    rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls
                    OR has_schema_privilege(rolname, 'public', 'CREATE')
                    OR has_table_privilege(rolname, 'schema_migrations', 'SELECT,INSERT,UPDATE,DELETE')
                    OR has_column_privilege(rolname, 'support_requests', 'scope', 'SELECT')
                ) FROM pg_roles WHERE rolname = 'vsa_runtime'""") == 't'
                run(compose+['up', '-d', '--no-build', '--pull', 'never', '--no-deps', 'api', 'worker', 'web'])
                validate_owned()
                assert len(resources('volume')) == 5
                volume_names = {
                    'api': {'deletion-control', 'input-staging', 'processing-work'},
                    'worker': {'deletion-control', 'input-staging', 'processing-work'},
                    'db': {'postgres-data'}, 'object-store': {'object-data'}, 'web': set(),
                }
                for service in ('api', 'worker', 'db', 'object-store', 'web'):
                    ports = run(['inspect', '--format', '{{json .HostConfig.PortBindings}}', f'{project}-{service}-1'])
                    assert json.loads(ports) == ({'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(port)}]} if service == 'web' else {})
                    networks = run(['inspect', '--format', '{{json .NetworkSettings.Networks}}', f'{project}-{service}-1'])
                    expected = {project+'_private', project+'_web_ingress'} if service == 'web' else {project+'_private'}
                    assert set(json.loads(networks)) == expected
                    container = f'{project}-{service}-1'
                    mounts = json.loads(run(['inspect', '--format', '{{json .Mounts}}', container]))
                    assert all(mount['Type'] == 'volume' for mount in mounts)
                    assert {mount['Name'] for mount in mounts} == {f'{project}_{name}' for name in volume_names[service]}
                    if service in ('api', 'worker', 'web'):
                        assert run(['exec', container, 'id', '-u']) == '10001'
                gate = compose+['--profile', 'maintenance', 'run', '--rm', '--no-deps', '--pull', 'never',
                                'maintenance', 'python', '-m', 'app.maintenance']
                origin = f'http://127.0.0.1:{port}'
                opener = build_opener(ProxyHandler({}))

                def wait_http(path, expected):
                    for _ in range(40):
                        try:
                            with opener.open(origin + path, timeout=2) as response:
                                status = response.status
                        except HTTPError as error:
                            status = error.code
                            error.close()
                        except URLError:
                            status = None
                        if status == expected:
                            return
                        time.sleep(.5)
                    raise RuntimeError(f'Expected HTTP {expected} for {path}; received {status}')

                assert json.loads(run(gate+['status']))['state'] == 'CLOSED'
                wait_http('/api/health/live', 200)
                wait_http('/api/health/ready', 503)
                wait_http('/api/auth/me', 503)
                assert json.loads(run(gate+['open']))['state'] == 'OPEN'
                wait_http('/api/health/ready', 200)
                browser = subprocess.run(
                    ['node', str(ROOT/'frontend/tests/s312-real.mjs'), origin, str(sample), project, args.docker_context],
                    cwd=ROOT, env=environment, text=True, encoding='utf-8', errors='replace',
                    capture_output=True, timeout=160,
                )
                if browser.returncode:
                    raise RuntimeError(f'Browser failed: {browser.stderr[-2500:]}')
                evidence = json.loads(browser.stdout)
                for _ in range(100):
                    if sql("SELECT count(*) FROM cleanup_jobs WHERE status='COMPLETED'") == '2':
                        break
                    time.sleep(.4)
                else:
                    raise AssertionError('Two deletion jobs did not complete')
                assert sql("SELECT count(*) FROM media_assets WHERE cleanup_state <> 'DELETED'") == '0'
                assert sql('SELECT count(*) FROM model_calls') == '0'
                assert sql('SELECT count(*) FROM task_results') == '0'
                bucket_check = "from app import object_store as s; assert s.client().list_objects_v2(Bucket=s.bucket())['KeyCount']==0"
                run(['exec', project+'-api-1', 'python', '-c', bucket_check])
                log = run(['logs', project+'-worker-1'])
                # stdout from the transport is separate from the Python application logger.
                assert [line for line in log.splitlines() if line.startswith('S312_MOCK_VALIDATED ')] == [
                    'S312_MOCK_VALIDATED 1', 'S312_MOCK_VALIDATED 2']
                evidence.update({'project': project, 'images': ids, 'migrations': 11, 'isolated_network': True,
                                 'migration_ledger_matches_frozen': True, 'database_permissions': True,
                                 'closed_gate_verified': True, 'non_root_app_services': True,
                                 'paid_requests': 0, 'cloud_fixture': args.cloud, 'physical_cleanup': 2,
                                 'validated_mock_requests': 2})
            finally:
                validate_owned()
                run(compose+['down', '--volumes', '--remove-orphans'])
            assert not any(resources(kind) for kind in ('container', 'volume', 'network'))
            evidence['test_resources_removed'] = True
            print(json.dumps(evidence), flush=True)
        finally:
            run(['image', 'rm', fixture_image])


if __name__ == '__main__':
    main()
