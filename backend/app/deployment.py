"""Explicit new-instance bootstrap. Never imports .env or touches legacy resources."""
import argparse
import json
import os
import re
from pathlib import Path
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


def configuration():
    if os.getenv('MAINTENANCE_GATE_REQUIRED') != 'true' or os.getenv('WP07_NEW_INSTANCE') != 'true':
        raise ValueError('NEW_INSTANCE_AND_GATE_REQUIRED')
    if os.getenv('MODEL_CALLS_ENABLED','false') != 'false':
        raise ValueError('OFFLINE_REQUIRED')
    dsns=[os.environ[k] for k in ('INITIAL_DATABASE_URL','MAINTENANCE_DATABASE_URL','RUNTIME_DATABASE_URL')]
    parts=[conninfo_to_dict(x) for x in dsns]
    if len({(x.get('host'),x.get('port','5432'),x.get('dbname')) for x in parts}) != 1:
        raise ValueError('DATABASE_IDENTITY_MISMATCH')
    names=[x.get('user','') for x in parts]
    if len(set(names)) != 3 or not all(re.fullmatch('[a-z][a-z0-9_]{0,62}',n) for n in names):
        raise ValueError('THREE_DISTINCT_ROLES_REQUIRED')
    if any(not x.get('password') for x in parts):raise ValueError('EXPLICIT_SECRETS_REQUIRED')
    if len({x['password'] for x in parts}) != 3:
        raise ValueError('THREE_DISTINCT_SECRETS_REQUIRED')
    root=Path(os.environ['DELETION_JOURNAL_DIR'])
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():raise ValueError('CONTROL_DIRECTORY_REQUIRED')
    if list(root.iterdir()):raise ValueError('NEW_EMPTY_CONTROL_REQUIRED')
    return dsns,parts,root


def _create_new_bucket():
    # Check existence only; never enumerate or adopt existing storage.
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    client=boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT_URL'],aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],region_name='us-east-1',config=Config(connect_timeout=3,read_timeout=5,retries={'max_attempts':1},s3={'addressing_style':'path'}))
    bucket=os.environ['S3_BUCKET']
    try:client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if str(exc.response['Error']['Code']) not in ('404','NoSuchBucket','NotFound'):raise
    else:raise ValueError('NEW_BUCKET_REQUIRED')
    client.create_bucket(Bucket=bucket)


def initialize():
    dsns,parts,root=configuration()
    init,maint,runtime=[x['user'] for x in parts]
    with psycopg.connect(dsns[0]) as c:
        existing=c.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','S','f')").fetchone()[0]
        if existing:raise ValueError('NEW_EMPTY_DATABASE_REQUIRED')
        if c.execute('SELECT 1 FROM pg_roles WHERE rolname IN (%s,%s)',(maint,runtime)).fetchone():raise ValueError('NEW_ROLES_REQUIRED')
        _create_new_bucket()
        for p in parts[1:]:
            c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS').format(sql.Identifier(p['user']),sql.Literal(p['password'])))
        database=c.execute('SELECT current_database()').fetchone()[0]
        c.execute(sql.SQL('ALTER DATABASE {} OWNER TO {}').format(sql.Identifier(database),sql.Identifier(maint)))
        c.execute(sql.SQL('ALTER SCHEMA public OWNER TO {}').format(sql.Identifier(maint)))
        c.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        c.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database),sql.Identifier(runtime)))
    from app.migrate import run_migrations
    with psycopg.connect(dsns[1]) as c:applied=run_migrations(c)
    with psycopg.connect(dsns[1]) as c:
        c.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(runtime)))
        c.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(runtime)))
        c.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(runtime)))
        for table in ('schema_migrations','task_consents','support_requests'):
            c.execute(sql.SQL('REVOKE ALL ON {} FROM {}').format(sql.Identifier(table),sql.Identifier(runtime)))
        c.execute(sql.SQL('GRANT DELETE,SELECT(task_id,user_id) ON task_consents TO {}').format(sql.Identifier(runtime)))
        c.execute(sql.SQL('GRANT DELETE,SELECT(task_id,user_id,closed_at),UPDATE(closed_at,close_reason) ON support_requests TO {}').format(sql.Identifier(runtime)))
    # Reconnect maintenance and runtime independently before establishing controls.
    with psycopg.connect(dsns[2]) as c:
        if c.execute('SELECT current_user').fetchone()[0] != runtime:raise ValueError('RUNTIME_IDENTITY_MISMATCH')
        if c.execute("SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()[0]:raise ValueError('UNSAFE_RUNTIME_ROLE')
    old=os.environ.get('DATABASE_URL');os.environ['DATABASE_URL']=dsns[1]
    try:
        from app.deletion_control import initialize as journal_initialize,_write
        journal_initialize()
        identity=json.loads((root/'identity.json').read_text())
        runtime_path=root/'runtime';runtime_path.mkdir(mode=0o700)
        _write(runtime_path/'producer-ready',identity)
        from app.maintenance import set_state
        with psycopg.connect(dsns[1],row_factory=dict_row) as c:set_state('CLOSED',connection=c)
    finally:
        if old is None:os.environ.pop('DATABASE_URL',None)
        else:os.environ['DATABASE_URL']=old
    return {'initialized':True,'migrations':applied,'roles':{'initial':init,'maintenance':maint,'runtime':runtime},'maintenance':'CLOSED','old_authorizations':'NOT_APPLICABLE_NEW_INSTANCE'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['initialize']);p.parse_args()
    try:print(json.dumps(initialize()));return 0
    except Exception:print('{"error":"INITIALIZATION_FAILED_CLOSED"}');return 2

if __name__=='__main__':raise SystemExit(main())
