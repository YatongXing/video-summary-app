"""Minimal database readiness probe for S3-01.

This module does not create schemas or task records.
"""

import os

import psycopg


def database_ready() -> bool:
    """Return whether PostgreSQL accepts a simple query, without exposing details."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return False

    try:
        with psycopg.connect(database_url, connect_timeout=2) as connection:
            connection.execute("SELECT 1")
    except (psycopg.Error, ValueError):
        return False
    return True
