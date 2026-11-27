"""Apply or verify the reviewed Stage C schema while the maintenance gate is closed."""
import argparse
import hashlib
import json

from app import maintenance
from app.auth import connect
from app.migrate import MIGRATIONS_DIR, run_migrations

UPGRADES = ('013_bilibili_input.sql', '014_bilibili_evidence.sql')


def approved_migrations():
    files = sorted(MIGRATIONS_DIR.glob('[0-9][0-9][0-9]_*.sql'))
    if [path.name[:3] for path in files] != [f'{n:03d}' for n in range(1, 15)] or tuple(path.name for path in files[-2:]) != UPGRADES:
        raise ValueError('Unexpected migrations in candidate image')
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def execute(apply):
    expected = approved_migrations()
    with connect() as connection:
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL statement_timeout = '60s'")
        maintenance.require_owner(connection)
        if not maintenance.required() or maintenance.read_state(connection)['state'] != 'CLOSED':
            raise ValueError('Close maintenance gate before proceeding')
        # Hold the same advisory lock as the general migration runner until commit.
        from app.migrate import _MIGRATION_LOCK_ID
        connection.execute('SELECT pg_advisory_xact_lock(%s)', (_MIGRATION_LOCK_ID,))
        ledger = {row['version']: row['sha256'] for row in connection.execute(
            'SELECT version,sha256 FROM schema_migrations')}
        schema = next((version for version in (12, 13, 14)
                       if ledger == {name: checksum for name, checksum in expected.items()
                                     if int(name[:3]) <= version}), None)
        if schema is None:
            raise ValueError('Database does not match an approved 012, 013, or 014 ledger')
        if schema == 14 or not apply:
            return {'schema': schema, 'applied': False}
        if connection.execute("SELECT 1 FROM tasks WHERE input_kind='URL' LIMIT 1").fetchone():
            raise ValueError('Unexpected legacy URL records')
        if connection.execute("""SELECT EXISTS (SELECT 1 FROM input_imports)
            OR EXISTS (SELECT 1 FROM tasks WHERE status IN ('QUEUED','PROCESSING')
                       AND deleted_at IS NULL AND deletion_requested_at IS NULL)
            OR EXISTS (SELECT 1 FROM model_calls WHERE deepseek_phase IN
                       ('ASR_SUBMITTING','ASR_WAITING','SUMMARY_SUBMITTING'))
            OR EXISTS (SELECT 1 FROM media_assets WHERE asset_kind IN
                       ('QWEN_TRANSFER','ASR_AUDIO_TRANSFER') AND cleanup_state<>'DELETED') AS busy""").fetchone()['busy']:
            raise ValueError('Resolve active imports, processing, and transfer cleanup first')
        # run_migrations uses positional rows for the migration ledger.
        from psycopg.rows import tuple_row
        connection.row_factory = tuple_row
        if run_migrations(connection) != list(UPGRADES[schema - 12:]):
            raise ValueError('Unexpected migration applied')
    return {'schema': 14, 'applied': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('verify', 'apply'))
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args.action == 'apply')))
    except ValueError as error:
        print(json.dumps({'error': str(error)}))
        return 2
    except Exception:
        print(json.dumps({'error': 'STAGE_C_SCHEMA_CHECK_FAILED_CLOSED'}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
