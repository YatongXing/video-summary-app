"""Apply versioned PostgreSQL schema migrations.

Run with ``python -m app.migrate`` and a DATABASE_URL supplied by the process
environment. This module never loads a local .env file or handles model keys.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

import psycopg


MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_MIGRATION_LOCK_ID = 77350302001


def run_migrations(connection: psycopg.Connection) -> list[str]:
    """Apply pending files once; reject changes to an already applied file."""
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError("No migration files found")

    applied: list[str] = []
    for path in files:
        content = path.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_ID,))
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    sha256 text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )"""
            )
            row = connection.execute(
                "SELECT sha256 FROM schema_migrations WHERE version = %s", (path.name,)
            ).fetchone()
            if row is not None:
                if row[0] != checksum:
                    raise RuntimeError(f"Applied migration changed: {path.name}")
                continue

            connection.execute(content.decode("utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations (version, sha256) VALUES (%s, %s)",
                (path.name, checksum),
            )
            applied.append(path.name)
    return applied


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(database_url) as connection:
            connection.execute("SET TIME ZONE 'UTC'")
            applied = run_migrations(connection)
    except (psycopg.Error, OSError, RuntimeError) as error:
        print(f"Migration failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print("Applied: " + (", ".join(applied) if applied else "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
