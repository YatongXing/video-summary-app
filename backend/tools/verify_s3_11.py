"""Exercise the new Worker and the real Web proxy in a disposable no-egress project."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4

root = Path(__file__).resolve().parents[2]
environment = {k:v for k,v in os.environ.items() if not k.startswith(('WP07_', 'VSA_S311_', 'COMPOSE_', 'DOCKER_'))}
environment['COMPOSE_DISABLE_ENV_FILE'] = '1'


HTTP = r"""
import base64, http.cookiejar, json, time, urllib.error, urllib.request
base='http://web:8080'
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
headers={}
def request(path, status, method='GET', body=None, additional=None):
    h={**headers,**(additional or {})}
    if isinstance(body,dict):
        body=json.dumps(body).encode()
        h['Content-Type']='application/json'
    try:r=client.open(urllib.request.Request(base+path,data=body,method=method,headers=h),timeout=10)
    except urllib.error.HTTPError as error:r=error
    with r:
        assert r.status==status,(path,status,r.status)
        return r.read()
assert b'assets/' in request('/',200)
request('/api/health/ready',200)
user={'email':'s311-fixture@acceptance.invalid','password':'Synthetic-S311-Only-Password'}
request('/api/auth/register',201,'POST',user)
session=json.loads(request('/api/auth/login',200,'POST',user))
headers['X-CSRF-Token']=session['csrf_token']
video=base64.b64decode(FIXTURE_B64)
task=json.loads(request('/api/inputs/upload',201,'POST',video,{'Content-Type':'application/octet-stream','X-Upload-Authorized':'true','X-Video-Filename':'s311-synthetic.mp4'}))
assert task['status']=='QUEUED' and task['has_audio']
for _ in range(50):
    row=next(t for t in json.loads(request('/api/tasks',200)) if t['id']==task['id'])
    if row['status'] in ('SUCCEEDED','FAILED'):break
    time.sleep(.5)
assert row['status']=='SUCCEEDED',row.get('failure_code')
for language in ('zh','en'):
    result=json.loads(request(f"/api/tasks/{task['id']}/result?language={language}",200))
    assert result['language']==language and result['offline_test'] is False
    export=json.loads(request(f"/api/tasks/{task['id']}/export?language={language}",200))
    assert export['summary']
assert len(request(f"/api/tasks/{task['id']}/video",206,additional={'Range':'bytes=0-29'}))==30
print(json.dumps({'task':task['id'],'uploaded_has_audio':True,'published_languages':['zh','en'],'range':206}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker-context', default='desktop-linux')
    parser.add_argument('--api-image', required=True)
    parser.add_argument('--worker-image', required=True)
    parser.add_argument('--web-image', required=True)
    parser.add_argument('--cloud', action='store_true', help='Exercise the OSS branch with a mock store')
    args = parser.parse_args()
    docker = ['docker', '--context', args.docker_context]
    cloud = args.cloud
    project = 'vsa-s311-test-' + uuid4().hex[:10]
    fixture_image = 'vsa-s311-fixture:' + project.removeprefix('vsa-s311-test-')

    def run(command, *, stdin=None, timeout=120, binary=False):
        result = subprocess.run(docker + command, input=stdin, cwd=root, env=environment,
                                encoding=None if binary else 'utf-8', errors=None if binary else 'replace',
                                capture_output=True, timeout=timeout)
        if result.returncode:
            error = result.stderr.decode('utf-8', errors='replace') if binary else result.stderr
            raise RuntimeError(f'Docker {command[0]} returned {result.returncode}: {error[-1800:]}')
        return result.stdout if binary else result.stdout.strip()

    for image in (args.api_image, args.worker_image, args.web_image):
        run(['image', 'inspect', '--format', '{{.Id}}', image])
    video = run(['run', '--rm', '--network', 'none', '--pull', 'never', '--entrypoint', 'ffmpeg', args.worker_image,
                 '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:r=10',
                 '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '3', '-c:v', 'libx264',
                 '-threads:v', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+frag_keyframe+empty_moov',
                 '-f', 'mp4', 'pipe:1'], binary=True)

    run(['build','--pull=false','--network','none','-t',fixture_image,'-f','-','backend/tests'],
        stdin=f'FROM {args.worker_image}\nCOPY test_qwen_adapter.py /srv/\n')
    try:
        run(['run','--rm','--network','none',fixture_image,'python','-m','unittest','-q','test_qwen_adapter'])
        for kind in (['ps','-aq'], ['volume','ls','-q'], ['network','ls','-q']):
            assert not run(kind+['--filter',f'label=com.docker.compose.project={project}'])
        with tempfile.TemporaryDirectory(prefix='vsa-s311-') as temporary:
            tmp = Path(temporary)
            config = {
                'WP07_PROJECT_NAME':project,'WP07_SYNTHETIC_ONLY':'true','WP07_DB':'s311_test',
                'WP07_INIT_PASSWORD':uuid4().hex,'WP07_MAINT_PASSWORD':uuid4().hex,'WP07_RUNTIME_PASSWORD':uuid4().hex,
                'WP07_OBJECT_ACCESS_KEY':'fixture'+uuid4().hex[:12],'WP07_OBJECT_SECRET_KEY':uuid4().hex,
                'WP07_APP_IMAGE':args.api_image,'WP07_WEB_IMAGE':args.web_image,
            }
            (tmp/'test.env').write_text(''.join(f'{k}={v}\n' for k,v in config.items()),encoding='ascii')
            worker = (
                "from unittest.mock import patch; from test_qwen_adapter import MockTransport,MockCloudStore; "
                "from app.qwen_adapter import QwenModelAdapter; from app.worker import main; "
                "patch('app.qwen_adapter.http.client.HTTPSConnection',MockTransport).start(); "
            )
            if cloud:
                worker += (
                    "QwenModelAdapter.max_inline_video_bytes=100; "
                    "patch('app.qwen_cloud.CloudVideoStore.from_environment',return_value=MockCloudStore()).start(); "
                )
            worker += "raise SystemExit(main())"
            (tmp/'test.yaml').write_text(f"""services:
  worker:
    image: {fixture_image}
    command: ["python", "-c", {json.dumps(worker)}]
    environment:
      WORKER_PROCESSOR_MODE: qwen
      MODEL_CALLS_ENABLED: "true"
      DASHSCOPE_API_KEY: sk-ws-synthetic-fixture
      S3_11_FIXTURE_SHA256: {hashlib.sha256(video).hexdigest()}
      S3_11_FIXTURE_CLOUD: "{'1' if cloud else ''}"
  web:
    ports: !reset []
networks:
  private:
    internal: true
""",encoding='ascii')
            compose=['compose','--project-name',project,'--env-file',str(tmp/'test.env'),
                     '-f',str(root/'deploy/compose.wp07.yaml'),'-f',str(tmp/'test.yaml')]
            try:
                run(compose+['up','-d','--no-build','--pull','never','--wait','--wait-timeout','60','db','object-store'])
                assert run(['network','inspect','--format','{{.Internal}}',project+'_private']) == 'true'
                for service in ('volume-init','initialize'):
                    run(compose+['--profile','initialize','run','--rm','--no-deps','--pull','never',service])
                run(compose+['up','-d','--no-build','--pull','never','--no-deps','api','worker','web'])
                assert run(['inspect','--format','{{len .HostConfig.PortBindings}}',project+'-web-1']) == '0'
                gate=compose+['--profile','maintenance','run','--rm','--no-deps','--pull','never','maintenance','python','-m','app.maintenance']
                assert json.loads(run(gate+['open']))['state']=='OPEN'
                for _ in range(15):
                    try:
                        run(['exec',project+'-api-1','python','-c',
                             "import urllib.request; assert urllib.request.urlopen('http://web:8080/api/health/ready',timeout=3).status==200"])
                        break
                    except RuntimeError:
                        time.sleep(1)
                else:
                    raise RuntimeError('Isolated Web/API did not become ready')
                source='FIXTURE_B64='+repr(base64.b64encode(video).decode('ascii'))+'\n'+HTTP
                evidence=json.loads(run(['exec','-i',project+'-api-1','python','-'],stdin=source,timeout=90))
                query="SELECT count(*)||'|'||min(request_id)||'|'||min(input_tokens)||'|'||min(output_tokens) FROM model_calls WHERE status='SUCCEEDED';"
                actual=run(['exec',project+'-db-1','psql','-X','-U','vsa_init','-d','s311_test','-At','-c',query])
                assert actual == '1|chatcmpl-fixture|270|21',actual
                cleanup = run(['exec',project+'-db-1','psql','-X','-U','vsa_init','-d','s311_test','-At','-c',
                    "SELECT cleanup_state FROM media_assets WHERE asset_kind='QWEN_TRANSFER';"])
                assert cleanup == ('DELETED' if cloud else ''),cleanup
                evidence.update({'project':project,'one_recorded_mock_call':True,'isolated_network':True,'paid_requests':0,
                                 'cloud_fixture':cloud,'cloud_cleanup':cleanup})
            finally:
                run(compose+['down','--volumes','--remove-orphans'])
        for kind in (['ps','-aq'],['volume','ls','-q'],['network','ls','-q']):
            assert not run(kind+['--filter',f'label=com.docker.compose.project={project}'])
        evidence['test_resources_removed'] = True
        print(json.dumps(evidence),flush=True)
    finally:
        run(['image','rm',fixture_image])


if __name__=='__main__':
    main()
