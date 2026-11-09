"""Verify a packaged one-click Start and Update using a disposable instance."""
import argparse
from copy import deepcopy
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request
from uuid import uuid4


MODEL = {
    'DASHSCOPE_API_KEY': 'sk-ws-synthetic-s312-config',
    'DASHSCOPE_BASE_URL': 'https://maas.qianwenaiapi.com/compatible-mode/v1',
    'QWEN_MODEL': 'qwen3.8-omni-flash',
    'QWEN_OSS_REGION': 'cn-hangzhou',
    'QWEN_OSS_BUCKET': 's312-synthetic',
    'QWEN_OSS_ACCESS_KEY_ID': 'synthetic-s312-id',
    'QWEN_OSS_ACCESS_KEY_SECRET': 'synthetic-s312-secret',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--prior-api-image')
    parser.add_argument('--prior-worker-image')
    parser.add_argument('--prior-web-image')
    args = parser.parse_args()
    prior_tags = {'api': args.prior_api_image, 'worker': args.prior_worker_image, 'web': args.prior_web_image}
    upgrading = any(prior_tags.values())
    if upgrading and not all(prior_tags.values()):
        parser.error('Supply all three prior application image tags')
    project = 'vsa-s312-launch-' + uuid4().hex[:8]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    manifest = json.loads((args.package/'manifest.json').read_text(encoding='utf-8-sig'))
    docker = ['docker', '--context', 'desktop-linux']
    env = {k: v for k, v in os.environ.items()
           if k.lower() != 'psmodulepath'
           and not k.startswith(('COMPOSE_', 'DOCKER_', 'VSA_S312_', 'VSA_S311_', 'WP07_'))}
    env.update(COMPOSE_DISABLE_ENV_FILE='1', WP07_PROJECT_NAME=project, WP07_HTTP_PORT=str(port),
               WP07_DB='video_summary_test', WP07_SYNTHETIC_ONLY='true',
               WP07_APP_IMAGE=manifest['images']['api']['tag'], WP07_WEB_IMAGE=manifest['images']['web']['tag'],
               VSA_S312_WORKER_IMAGE=manifest['images']['worker']['tag'])
    services = ('api', 'worker', 'web', 'db', 'object-store')
    image_names = dict(zip(services, ('api', 'worker', 'web', 'db', 'object_store')))

    def run(command, *, binary=False):
        result = subprocess.run(command, env=env, capture_output=True, timeout=240,
                                encoding=None if binary else 'utf-8', errors=None if binary else 'replace')
        if result.returncode:
            raise RuntimeError(f'{command[0]} exited {result.returncode}: {result.stderr[-2500:]}')
        return result.stdout if binary else result.stdout.strip()

    def owned(kind):
        command = {'container': ['ps', '-aq'], 'volume': ['volume', 'ls', '-q'], 'network': ['network', 'ls', '-q']}[kind]
        return run(docker + command + ['--filter', f'label=com.docker.compose.project={project}']).splitlines()

    def inspect(service):
        return json.loads(run(docker+['container', 'inspect', project+'-'+service+'-1']))[0]

    def volumes():
        return {v: run(docker+['volume', 'inspect', '--format', '{{.CreatedAt}}|{{.Mountpoint}}', v])
                for v in owned('volume')}

    def api(path, status=200, method='GET', data=None, headers=None):
        headers = {'Origin': origin, **(headers or {})}
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        with client.open(Request(origin+'/api'+path, data=data, method=method, headers=headers), timeout=15) as response:
            assert response.status == status, path
            return response.read()

    assert not any(owned(kind) for kind in ('container', 'volume', 'network'))
    starting_manifest = deepcopy(manifest)
    if upgrading:
        for service, tag in prior_tags.items():
            identity = run(docker+['image', 'inspect', '--format', '{{.Id}}', tag])
            assert manifest['formal_previous'][service] == identity, service
            assert identity != manifest['images'][service]['id'], service
            starting_manifest['images'][service] = {'id': identity, 'tag': tag}
    with tempfile.TemporaryDirectory(prefix='vsa-s312-launch-') as temp:
        root = Path(temp)
        shutil.copytree(args.package/'deploy', root/'deploy')
        (root/'manifest.json').write_text(json.dumps(starting_manifest), encoding='utf-8')
        (root/'.env').write_text(''.join(f'{name}={value}\n' for name, value in MODEL.items()), encoding='ascii')
        state = root/'state'
        private_env = state/(project+'.env')
        launch = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'RemoteSigned', '-File',
                  str(root/'deploy/Start-S3-12.ps1'), '-ProjectName', project, '-HttpPort', str(port),
                  '-StateDirectory', str(state)]
        compose = docker+['compose', '--project-name', project, '--env-file', str(private_env)]
        for filename in ('compose.wp07.yaml', 'compose.s3-12.yaml', 'compose.s3-12-isolated.yaml'):
            compose += ['-f', str(root/'deploy'/filename)]
        try:
            assert 'Worker: offline. Gate: OPEN.' in run(launch+['-Action', 'Start'])
            assert run(docker+['network', 'inspect', '--format', '{{.Internal}}', project+'_private']) == 'true'
            original = {service: inspect(service) for service in services}
            for service in services:
                assert original[service]['Image'] == starting_manifest['images'][image_names[service]]['id']
            original_volumes = volumes()
            assert len(original_volumes) == 5
            old_worker_env = dict(item.split('=', 1) for item in original['worker']['Config']['Env'])
            assert old_worker_env['WORKER_PROCESSOR_MODE'] == 'offline'
            assert not old_worker_env.get('DASHSCOPE_API_KEY') and not old_worker_env.get('QWEN_OSS_ACCESS_KEY_SECRET')

            client = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
            account = {'email': 'launch@s312.invalid', 'password': 'S312-Launch-Synthetic!'}
            api('/auth/register', 201, 'POST', account)
            session = json.loads(api('/auth/login', 200, 'POST', account))
            sample = run(docker+['run', '--rm', '--network', 'none', '--pull', 'never', '--entrypoint', 'ffmpeg',
                                 manifest['images']['worker']['tag'], '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                                 '-i', 'color=c=blue:s=320x240:r=10', '-f', 'lavfi', '-i', 'sine=frequency=440',
                                 '-t', '3', '-c:v', 'libx264', '-threads:v', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                                 '-movflags', '+frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'], binary=True)
            task = json.loads(api('/inputs/upload', 201, 'POST', sample, {
                'Content-Type': 'application/octet-stream', 'X-Upload-Authorized': 'true',
                'X-Video-Filename': 'upgrade-synthetic.mp4', 'X-CSRF-Token': session['csrf_token']}))
            task_id = task['id']
            for _ in range(90):
                row = next(row for row in json.loads(api('/tasks')) if row['id'] == task_id)
                if row['status'] in ('SUCCEEDED', 'FAILED'):
                    break
                time.sleep(.5)
            assert row['status'] == 'SUCCEEDED', row.get('failure_code')
            result = api(f'/tasks/{task_id}/result?language=zh')
            original_bytes = api(f'/tasks/{task_id}/video', 206, headers={'Range': 'bytes=0-29'})
            assert original_bytes == sample[:30]
            sql = docker+['exec', project+'-db-1', 'psql', '-X', '-U', 'vsa_init', '-d', 'video_summary_test', '-At',
                          '-v', 'ON_ERROR_STOP=1', '-c']
            calls = run(sql+['SELECT count(*) FROM model_calls'])

            (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            assert 'Worker: qwen. Gate: OPEN.' in run(launch+['-Action', 'Update', '-TestQwen'])
            updated = {service: inspect(service) for service in services}
            assert volumes() == original_volumes
            assert updated['worker']['Id'] != original['worker']['Id']
            for service in services:
                assert updated[service]['Image'] == manifest['images'][image_names[service]]['id'], service
                if upgrading and service in prior_tags:
                    assert updated[service]['Id'] != original[service]['Id'], service
                elif service != 'worker':
                    assert updated[service]['Id'] == original[service]['Id'], service
                cfg = dict(item.split('=', 1) for item in updated[service]['Config']['Env'])
                if service == 'worker':
                    assert all(cfg.get(name) == value for name, value in MODEL.items()), 'Worker provider config mismatch'
                    assert cfg['WORKER_PROCESSOR_MODE'] == 'qwen' and cfg['MODEL_CALLS_ENABLED'] == 'true'
                else:
                    assert not set(cfg).intersection(MODEL), 'Provider config leaked beyond the Worker'
            run(docker+['exec', project+'-worker-1', 'python', '-c',
                        'from app.worker import _configured_processor; assert _configured_processor().keywords["adapter"].cloud'])
            assert json.loads(api('/auth/me'))['user']['email'] == account['email']
            assert api(f'/tasks/{task_id}/result?language=zh') == result
            assert api(f'/tasks/{task_id}/video', 206, headers={'Range': 'bytes=0-29'}) == original_bytes
            assert run(sql+['SELECT count(*) FROM model_calls']) == calls
            run(docker+['stop']+[updated[service]['Id'] for service in services])
            assert 'S3-12 resumed.' in run(launch+['-Action', 'Start'])
            assert volumes() == original_volumes
            for service in services:
                assert inspect(service)['Id'] == updated[service]['Id']
            assert api(f'/tasks/{task_id}/result?language=zh') == result
            assert api(f'/tasks/{task_id}/video', 206, headers={'Range': 'bytes=0-29'}) == original_bytes
            evidence = {'project': project, 'offline_start': True, 'synthetic_qwen_update': True,
                        'app_services_replaced': ['api', 'worker', 'web'] if upgrading else ['worker'],
                        'db_object_store_preserved': True, 'volumes_preserved': len(original_volumes),
                        'account_session_result_and_media_preserved': True, 'provider_config_worker_only': True,
                        'resumed_same_containers': True, 'isolated_network': True, 'paid_requests': 0}
        finally:
            for kind in ('container', 'volume', 'network'):
                for resource in owned(kind):
                    label = ('{{index .Config.Labels "vsa.synthetic_only"}}' if kind == 'container'
                             else '{{index .Labels "vsa.synthetic_only"}}')
                    assert run(docker+[kind, 'inspect', '--format', label, resource]) == 'true'
            if any(owned(kind) for kind in ('container', 'volume', 'network')):
                run(compose+['down', '--volumes', '--remove-orphans'])
        assert not any(owned(kind) for kind in ('container', 'volume', 'network'))
        evidence['test_resources_removed'] = True
        print(json.dumps(evidence))


if __name__ == '__main__':
    main()
