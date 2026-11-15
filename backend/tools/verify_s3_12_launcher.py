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
    parser.add_argument('--stage-b', action='store_true', help='Verify isolated 011 → 012 and compatible feature rollback')
    parser.add_argument('--rollback-package', type=Path, help='Previous 012 package for isolated image rollback')
    args = parser.parse_args()
    if args.rollback_package and not args.stage_b:
        parser.error('--rollback-package requires --stage-b')
    prior_tags = {'api': args.prior_api_image, 'worker': args.prior_worker_image, 'web': args.prior_web_image}
    upgrading = any(prior_tags.values())
    if (upgrading or args.stage_b) and not all(prior_tags.values()):
        parser.error('Supply all three prior application image tags')
    project = 'vsa-s312-launch-' + uuid4().hex[:8]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    manifest = json.loads((args.package/'manifest.json').read_text(encoding='utf-8-sig'))
    rollback = None
    if args.rollback_package:
        rollback = json.loads((args.rollback_package/'manifest.json').read_text(encoding='utf-8-sig'))
        assert rollback['migrations'] == manifest['migrations'] and len(manifest['migrations']) == 12
        assert all(rollback['images'][service] == manifest['images'][service] for service in ('db', 'object_store'))
        rollback['files'] = manifest['files']
        rollback['formal_previous'] = {service: manifest['images'][service]['id'] for service in prior_tags}
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
    if args.stage_b:
        extension = '012_task_model_selection.sql'
        assert extension in manifest['migrations']
        starting_manifest['migrations'].pop(extension)
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
            assert json.loads(api('/config/public'))['support_email'] is None
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
            feedback = 'feedback+launcher@example.invalid'
            with (root/'.env').open('a', encoding='ascii') as config:
                config.write(f'SUPPORT_EMAIL="{feedback}"\n')
            if args.stage_b:
                probe = subprocess.run(launch+['-Action', 'Update', '-TestQwen'], env=env,
                                       capture_output=True, text=True, timeout=90)
                assert probe.returncode and 'Migration bytes or database ledger' in probe.stderr
                assert all(inspect(service)['Id'] == original[service]['Id'] for service in services)
                gate_command = compose+['--profile','maintenance','run','--rm','--no-deps','--pull','never','maintenance']
                def gate(state):
                    return json.loads(run(gate_command+['python','-m','app.maintenance',state]))['state']
                assert gate('status') == 'OPEN' and gate('close') == 'CLOSED'
                migrate = ("import os, psycopg; from app import maintenance; from app.migrate import run_migrations; "
                           "assert maintenance.read_state()['state']=='CLOSED'; "
                           "c=psycopg.connect(os.environ['MAINTENANCE_DATABASE_URL'],autocommit=True); "
                           "maintenance.require_owner(c); assert run_migrations(c)==['012_task_model_selection.sql']; "
                           "assert run_migrations(c)==[]; c.close()")
                run(gate_command+['python','-c',migrate])
                assert 'Worker: qwen. Gate: CLOSED.' in run(launch+['-Action', 'Update', '-TestQwen'])
                assert gate('status') == 'CLOSED' and gate('open') == 'OPEN'
            else:
                assert 'Worker: qwen. Gate: OPEN.' in run(launch+['-Action', 'Update', '-TestQwen'])
            updated = {service: inspect(service) for service in services}
            assert volumes() == original_volumes
            assert updated['worker']['Id'] != original['worker']['Id']
            for service in services:
                assert updated[service]['Image'] == manifest['images'][image_names[service]]['id'], service
                if upgrading and service in prior_tags:
                    assert updated[service]['Id'] != original[service]['Id'], service
                elif service in ('api', 'worker'):
                    assert updated[service]['Id'] != original[service]['Id'], service
                else:
                    assert updated[service]['Id'] == original[service]['Id'], service
                cfg = dict(item.split('=', 1) for item in updated[service]['Config']['Env'])
                if service == 'worker':
                    assert all(cfg.get(name) == value for name, value in MODEL.items()), 'Worker provider config mismatch'
                    assert cfg['WORKER_PROCESSOR_MODE'] == 'qwen' and cfg['MODEL_CALLS_ENABLED'] == 'true'
                else:
                    assert not set(cfg).intersection(MODEL), 'Provider config leaked beyond the Worker'
            run(docker+['exec', project+'-worker-1', 'python', '-c',
                        'from app.worker import _configured_processor; assert _configured_processor() is not None'])
            assert json.loads(api('/config/public'))['support_email'] == feedback
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
            assert json.loads(api('/config/public'))['support_email'] == feedback
            if args.stage_b:
                qwen, deepseek = 'qwen3.8-omni-flash', 'deepseek-v4.1-flash'
                if rollback:
                    settings = {name: value for name, value in MODEL.items() if not name.startswith('QWEN_OSS_')}
                    settings['SUPPORT_EMAIL'] = feedback
                    (root/'.env').write_text(''.join(f'{name}={value}\n' for name, value in settings.items()),
                                             encoding='ascii')
                assert json.loads(api('/config/public'))['available_models'] == [qwen]
                assert run(sql+[f"SELECT requested_model FROM tasks WHERE id='{task_id}'"]) == qwen
                assert 'Worker: qwen. Gate: OPEN.' in run(launch+['-Action','Update','-TestQwen','-EnableDeepSeek'])
                assert set(json.loads(api('/config/public'))['available_models']) == {qwen, deepseek}
                if rollback:
                    settings = dict(item.split('=', 1) for item in inspect('worker')['Config']['Env'])
                    assert not any(settings.get(key) for key in MODEL if key.startswith('QWEN_OSS_'))
                failed_id = uuid4()
                run(sql+[f"INSERT INTO tasks(id,user_id,input_kind,status,requested_model) "
                          f"SELECT '{failed_id}',user_id,'LOCAL','FAILED','{deepseek}' FROM tasks WHERE id='{task_id}'"])
                if rollback:
                    success_id = uuid4()
                    run(sql+[f"INSERT INTO tasks(id,user_id,input_kind,status,requested_model,display_filename,duration_seconds) "
                              f"SELECT '{success_id}',user_id,'LOCAL','SUCCEEDED','{deepseek}',display_filename,duration_seconds "
                              f"FROM tasks WHERE id='{task_id}'"])
                    run(sql+[f"INSERT INTO task_results(task_id,user_id,language,lease_generation,export_document,presentation,synthetic) "
                              f"SELECT '{success_id}',user_id,language,lease_generation,export_document,presentation,true "
                              f"FROM task_results WHERE task_id='{task_id}'"])
                    deepseek_exports = {language: api(f'/tasks/{success_id}/export?language={language}')
                                        for language in ('zh', 'en')}
                asset_id = uuid4()
                run(sql+[f"INSERT INTO media_assets(id,task_id,user_id,asset_kind,storage_key,storage_backend,storage_bucket) "
                          f"SELECT '{asset_id}',id,user_id,'ASR_AUDIO_TRANSFER','fixture-only','S3:'||repeat('0',64),'synthetic-bucket' "
                          f"FROM tasks WHERE id='{failed_id}'"])
                def expect_busy():
                    blocked = subprocess.run(launch+['-Action','Update','-TestQwen'], env=env,
                                             capture_output=True, text=True, timeout=90)
                    assert blocked.returncode and 'pending OSS cleanup and unsettled model requests' in blocked.stderr
                    assert gate('status') == 'OPEN'
                expect_busy()
                run(sql+[f"DELETE FROM media_assets WHERE id='{asset_id}'"])
                run(sql+[f"UPDATE model_calls SET deepseek_phase='SUMMARY_SUBMITTING' WHERE task_id='{task_id}'"])
                expect_busy()
                run(sql+[f"UPDATE model_calls SET deepseek_phase=NULL WHERE task_id='{task_id}'"])
                if rollback:
                    (root/'manifest.json').write_text(json.dumps(rollback), encoding='utf-8')
                assert 'Worker: qwen. Gate: OPEN.' in run(launch+['-Action','Update','-TestQwen'])
                if rollback:
                    for service in services:
                        assert inspect(service)['Image'] == rollback['images'][image_names[service]]['id']
                    assert all(inspect(service)['Id'] == original[service]['Id'] for service in ('db', 'object-store'))
                assert json.loads(api('/config/public'))['available_models'] == [qwen]
                export = api(f'/tasks/{task_id}/export?language=en')
                assert export.startswith(b'## Summary') and b'## Chapters' in export
                if rollback:
                    assert run(sql+[f"SELECT requested_model FROM tasks WHERE id='{success_id}'"]) == deepseek
                    for language, content in deepseek_exports.items():
                        assert api(f'/tasks/{success_id}/export?language={language}') == content
                from urllib.error import HTTPError
                try:
                    api(f'/tasks/{failed_id}/retry',409,'POST',{'request_key':str(uuid4())},
                        {'X-CSRF-Token': session['csrf_token']})
                except HTTPError as e:
                    assert e.code == 409
                else:
                    raise AssertionError('A pinned DeepSeek retry was accepted by the compatibility profile')
                assert run(sql+['SELECT count(*) FROM model_calls']) == calls
                assert run(sql+["SELECT count(*) FROM schema_migrations"]) == '12'
            model_env = root/'.env'
            model_env.write_text(model_env.read_text(encoding='ascii').replace(feedback, ''), encoding='ascii')
            assert 'Worker: qwen. Gate: OPEN.' in run(launch+['-Action', 'Update', '-TestQwen'])
            assert json.loads(api('/config/public'))['support_email'] is None
            assert volumes() == original_volumes
            evidence = {'project': project, 'offline_start': True, 'synthetic_qwen_update': True,
                        'app_services_replaced': ['api', 'worker', 'web'] if upgrading else ['api', 'worker'],
                        'db_object_store_preserved': True, 'volumes_preserved': len(original_volumes),
                        'account_session_result_and_media_preserved': True, 'provider_config_worker_only': True,
                        'resumed_same_containers': True, 'feedback_config_set_and_cleared': True, 'isolated_network': True, 'paid_requests': 0}
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
        if args.stage_b:
            evidence.update(isolated_011_to_012=True, old_task_default_qwen=True, gate_preserved_on_migration=True,
                            feature_off_rollback=True, deepseek_retry_rejected_by_rollback=True,
                            old_markdown_preserved=True, pending_asr_and_unknown_calls_block_upgrade=True,
                            no_provider_requests=True)
        if rollback:
            evidence.update(deepseek_without_bucket_credentials=True, deepseek_bilingual_markdown_preserved=True,
                            rollback_image_ids={service: rollback['images'][service]['id'] for service in prior_tags})
        print(json.dumps(evidence))


if __name__ == '__main__':
    main()
