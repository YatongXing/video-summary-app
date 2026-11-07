"""Read-only Windows/Docker local observer. Notifications go ONLY to a test file."""
import argparse
import datetime
import json
import math
import os
import sys
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

PROBE_BUDGET_SECONDS = 30

# An HTTP socket timeout alone does not bound trickled response headers.
# The child has no proxy or redirect handling and reports only a status code.
HTTP_CHECK = '''
from http.client import HTTPConnection
import sys

host, port, path = sys.argv[1:]
connection = HTTPConnection(host, int(port))
try:
    connection.request('GET', path)
    print(connection.getresponse().status)
finally:
    connection.close()
'''

# Reuse the existing single-attempt client.
STORAGE_CHECK = '''
import json
from app.object_store import bucket, cleanup_client
from botocore.exceptions import ClientError

try:
    cleanup_client().head_bucket(Bucket=bucket())
except ClientError:
    print(json.dumps({'ok': False, 'error': 'STORAGE_AUTHORIZATION_FAILED'}))
except Exception:
    print(json.dumps({'ok': False, 'error': 'STORAGE_UNAVAILABLE'}))
else:
    print(json.dumps({'ok': True}))
'''


class ProbeDeadline(TimeoutError):
    pass


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise ProbeDeadline
    return seconds


def run_probe(command, deadline, limit):
    budget = remaining(deadline)
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=min(budget, limit)
        )
    except subprocess.TimeoutExpired:
        if budget <= limit:
            raise ProbeDeadline from None
        raise
    remaining(deadline)
    return result


def docker(*args, deadline):
    p=run_probe(['docker',*args],deadline,12)
    if p.returncode:raise ValueError('DOCKER_PROBE_FAILED')
    return p.stdout.strip()

def load(path):
    state=json.loads(Path(path).read_text(encoding='utf-8'))
    identifier=state['id'];synthetic=state.get('synthetic_only') is True
    if state.get('package')!='CR-WP07-A':raise ValueError('STATE_IDENTITY_REQUIRED')
    if synthetic:
        if not identifier.startswith('vsa-wp07a-test-'):raise ValueError('TEST_PREFIX_REQUIRED')
    elif not(identifier.startswith('vsa-local') and state.get('formal_target_confirmed') is True):
        raise ValueError('FORMAL_TARGET_NOT_CONFIRMED')
    u=urlsplit(state['api_url'])
    if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost') or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        raise ValueError('LOOPBACK_REQUIRED')
    prefix=state.get('api_path_prefix','/api')
    if prefix not in ('','/api'):raise ValueError('API_PREFIX_REQUIRED')
    c=state['containers'];names=[c['api'],c['db'],c['s3'],*c['workers']]
    if prefix=='/api' and not c.get('web'):raise ValueError('WEB_RESOURCE_REQUIRED')
    if c.get('web'):names.append(c['web'])
    if len(set(names))!=len(names) or not c['workers'] or any(not n.startswith(identifier+'-') for n in names):raise ValueError('RESOURCE_SCOPE_REQUIRED')
    return state

def inspected(name,state,deadline):
    data=json.loads(docker('inspect','--format','{"id":{{json .Id}},"labels":{{json .Config.Labels}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"ports":{{json .NetworkSettings.Ports}}}',name,deadline=deadline))
    labels=data['labels'] or {}
    if labels.get('vsa.work_package')!='CR-WP07-A' or labels.get('vsa.synthetic_only')!=str(state['synthetic_only']).lower():raise ValueError('RESOURCE_OWNERSHIP_MISMATCH')
    return data

def endpoint_owned(state,deadline):
    prefix=state.get('api_path_prefix','/api')
    name=state['containers']['api'] if prefix=='' else state['containers'].get('web')
    if not name or not name.startswith(state['id']+'-'):return False
    item=inspected(name,state,deadline)
    u=urlsplit(state['api_url']);port=str(u.port or 80)
    key='8000/tcp' if prefix=='' else '8080/tcp'
    return any(b.get('HostPort')==port and b.get('HostIp')=='127.0.0.1' for b in (item['ports'].get(key) or []))


def python_exec(name,code,deadline):
    # Bound the Linux child even when killing the Docker CLI leaves it running.
    return docker('exec',name,'python','-c','import signal; signal.alarm(8)\n'+code,deadline=deadline)


def json_exec(name,code,deadline):
    # Only fixed non-content diagnostic code is sent; no environment is returned.
    return json.loads(python_exec(name,code,deadline))

def probe_until(state,deadline):
    c=state['containers'];faults=[];running={}
    for kind,names in [('api',[c['api']]),('database',[c['db']]),('storage',[c['s3']]),('worker',c['workers'])]:
        for name in names:
            try:
                item=inspected(name,state,deadline);running[name]=item['running'] and not item['paused']
                if item['paused']:faults.append(kind.upper()+'_PAUSED')
                elif not item['running']:faults.append(kind.upper()+'_STOPPED')
            except ProbeDeadline:raise
            except Exception:running[name]=False;faults.append(kind.upper()+'_UNKNOWN')
    maintenance='UNKNOWN'
    if running[c['api']]:
        try:
            data=json_exec(c['api'],"import json;from app.maintenance import read_state;print(json.dumps(read_state()))",deadline)
            maintenance=data.get('state','UNKNOWN')
            if maintenance not in ('OPEN','CLOSED'):maintenance='UNKNOWN'
        except ProbeDeadline:raise
        except Exception:pass
    if maintenance=='UNKNOWN':faults.append('CONTROL_UNKNOWN')
    try:endpoint_trusted=endpoint_owned(state,deadline)
    except ProbeDeadline:raise
    except Exception:endpoint_trusted=False
    if not endpoint_trusted:faults.append('ENDPOINT_OWNERSHIP_MISMATCH')
    url = urlsplit(state['api_url'])

    def http(path):
        if not endpoint_trusted:
            return False
        route = state.get('api_path_prefix','/api') + path
        try:
            response = run_probe(
                [sys.executable, '-I', '-c', HTTP_CHECK, '127.0.0.1', str(url.port or 80), route],
                deadline,
                4,
            )
            return response.returncode == 0 and response.stdout.strip() == '200'
        except ProbeDeadline:
            raise
        except Exception:
            return False
    if not http('/health/live'):faults.append('API_LIVENESS_FAILED')
    if maintenance=='OPEN' and not http('/health/ready'):faults.append('API_NOT_READY')
    if running[c['db']]:
        try:docker('exec',c['db'],'pg_isready','-q',deadline=deadline)
        except ProbeDeadline:raise
        except Exception:faults.append('DATABASE_UNAVAILABLE')
    if running[c['s3']]:
        try:docker('exec',c['s3'],'curl','-fsS','--max-time','4','http://127.0.0.1:9000/health',deadline=deadline)
        except ProbeDeadline:raise
        except Exception:faults.append('STORAGE_UNAVAILABLE')
    # Runtime-authenticated metadata probe remains necessary while CLOSED.
    # It reads no object body or listing and emits only fixed classifications.
    if running[c['api']]:
        try:
            storage=json_exec(c['api'],STORAGE_CHECK,deadline)
            if not storage.get('ok'):faults.append(storage.get('error') if storage.get('error') in ('STORAGE_AUTHORIZATION_FAILED','STORAGE_UNAVAILABLE') else 'STORAGE_UNAVAILABLE')
        except ProbeDeadline:raise
        except Exception:faults.append('STORAGE_UNAVAILABLE')
    if maintenance=='OPEN':
        for name in c['workers']:
            if running[name]:
                try:python_exec(name,"import sys; sys.argv.append('--healthcheck'); from app.worker import main; raise SystemExit(main())",deadline)
                except ProbeDeadline:raise
                except Exception:faults.append('WORKER_UNHEALTHY')
        if running[c['api']]:
            try:
                # Do not call cleanup health if control is untrusted: its legacy
                # runtime helper may otherwise recreate a missing directory.
                h=json_exec(c['api'],"import json;from app.cleanup_ops import health;print(json.dumps(health()))",deadline)
                if not h.get('healthy'):faults.append('CLEANUP_UNHEALTHY')
                if h.get('persistent_faults',0):faults.append('PERSISTENT_CLEANUP_FAULT')
                if h.get('import_faults',0):faults.append('IMPORT_CLEANUP_FAULT')
            except ProbeDeadline:raise
            except Exception:faults.append('CLEANUP_UNKNOWN')
    return {'maintenance':maintenance,'faults':sorted(set(faults)),'healthy':not faults and maintenance=='OPEN','business_ready':not faults and maintenance=='OPEN','test_notification_channel':True}

def probe(state):
    deadline = time.monotonic() + PROBE_BUDGET_SECONDS
    try:
        result = probe_until(state, deadline)
        remaining(deadline)
        return result
    except ProbeDeadline:
        return {
            'maintenance': 'UNKNOWN',
            'faults': ['PROBE_DEADLINE'],
            'healthy': False,
            'business_ready': False,
            'test_notification_channel': True,
        }


def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8') as f:json.dump(data,f);f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)

def observe(state,events,repeat_seconds=60):
    events=Path(events);history=events.with_suffix(events.suffix+'.state.json')
    old={}
    if history.exists():
        try:
            old=json.loads(history.read_text(encoding='utf-8'))
            if old.get('id')!=state['id']:raise ValueError('MONITOR_STATE_IDENTITY_MISMATCH')
        except (json.JSONDecodeError,OSError):raise ValueError('MONITOR_STATE_UNTRUSTED') from None
    result=probe(state);now=time.time();faults=result['faults'];kind=None
    if faults:
        if faults!=old.get('faults',[]):kind='FAULT'
        elif now-old.get('last_notification',0)>=repeat_seconds:kind='CONTINUING'
    elif old.get('faults'):kind='RECOVERED'
    if kind:
        record={'id':state['id'],'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':kind,'faults':faults,'maintenance':result['maintenance'],'channel':'LOCAL_TEST_FILE','formal_recipient_confirmed':False}
        events.parent.mkdir(parents=True,exist_ok=True)
        with events.open('a',encoding='utf-8') as f:f.write(json.dumps(record)+'\n');f.flush();os.fsync(f.fileno())
    save(history,{'id':state['id'],'faults':faults,'last_notification':now if kind else old.get('last_notification',0),'last_observed':now})
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--state',required=True,type=Path);p.add_argument('--events',required=True,type=Path);p.add_argument('--once',action='store_true');p.add_argument('--interval',type=float,default=30);p.add_argument('--repeat-seconds',type=float,default=60);a=p.parse_args()
    if not math.isfinite(a.interval) or not 1<=a.interval<=60 or not math.isfinite(a.repeat_seconds) or a.repeat_seconds<1:p.error('invalid polling period')
    try:
        state=load(a.state)
        while True:
            started = time.monotonic()
            result=observe(state,a.events,a.repeat_seconds);print(json.dumps(result),flush=True)
            if a.once:return 0 if not result['faults'] else 2
            time.sleep(max(0, a.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:return 0
    except Exception:print('{"error":"MONITOR_PROBE_FAILED"}',flush=True);return 4

if __name__=='__main__':raise SystemExit(main())
