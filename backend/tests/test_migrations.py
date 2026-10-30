"""S3-02 PostgreSQL integration checks against an explicit disposable test DB.

Set TEST_DATABASE_URL to a database whose name ends in ``_test``. These checks
never read the application DATABASE_URL or any local environment file.
"""

import os
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest


EXPECTED_TABLES = {
    "users",
    "sessions",
    "tasks",
    "task_attempts",
    "quota_events",
    "retry_events",
    "media_assets",
    "evaluation_links",
    "task_consents",
    "audit_events",
    "cleanup_jobs",
    "cleanup_alerts",
    "schema_migrations",
}


@pytest.fixture(scope="module")
def test_connection() -> psycopg.Connection:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL *_test database")
    database_name = conninfo_to_dict(url).get("dbname", "")
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name an isolated *_test database")

    from app.migrate import run_migrations

    with psycopg.connect(url, autocommit=True) as connection:
        run_migrations(connection)
        yield connection


def test_migrations_replay_without_duplicate_versions(
    test_connection: psycopg.Connection,
) -> None:
    from app.migrate import run_migrations

    with test_connection.cursor() as cursor:
        cursor.execute("SELECT version, sha256 FROM schema_migrations ORDER BY version")
        before = cursor.fetchall()
    assert before, "The migration runner must record applied versions"

    run_migrations(test_connection)
    with test_connection.cursor() as cursor:
        cursor.execute("SELECT version, sha256 FROM schema_migrations ORDER BY version")
        after = cursor.fetchall()
    assert after == before


def test_minimal_entity_set_exists(test_connection: psycopg.Connection) -> None:
    with test_connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
        )
        actual = {row[0] for row in cursor.fetchall()}
    assert EXPECTED_TABLES <= actual


def test_task_scoped_tables_have_user_and_task_scope(
    test_connection: psycopg.Connection,
) -> None:
    directly_task_scoped = EXPECTED_TABLES - {
        "users", "sessions", "tasks", "schema_migrations", "audit_events", "cleanup_alerts"
    }
    with test_connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
        columns: dict[str, set[str]] = {}
        for table, column in cursor.fetchall():
            columns.setdefault(table, set()).add(column)

        cursor.execute(
            "SELECT child.relname, pg_get_constraintdef(con.oid) "
            "FROM pg_constraint AS con "
            "JOIN pg_class AS child ON child.oid = con.conrelid "
            "JOIN pg_class AS parent ON parent.oid = con.confrelid "
            "WHERE con.contype = 'f' AND parent.relname = 'tasks' "
            "AND child.relnamespace = current_schema()::regnamespace"
        )
        task_fks: dict[str, list[str]] = {}
        for table, definition in cursor.fetchall():
            task_fks.setdefault(table, []).append(definition)

    for table in directly_task_scoped:
        assert {"user_id", "task_id"} <= columns.get(table, set()), table
        assert any(
            "task_id" in definition and "user_id" in definition
            for definition in task_fks.get(table, [])
        ), f"{table} needs a user-scoped task foreign key"

    # Deletion audit deliberately has no task FK: it outlives the 30-day task
    # metadata for its separate 90-day term. Alerts inherit task scope through
    # their composite cleanup-job FK.
    assert {"target_user_id", "task_id"} <= columns["audit_events"]
    assert {"user_id", "task_id", "cleanup_job_id"} <= columns["cleanup_alerts"]
    assert any(
        "cleanup_job_id" in definition and "task_id" in definition
        and "user_id" in definition
        for definition in _foreign_key_definitions(test_connection, "cleanup_alerts", "cleanup_jobs")
    )


def _foreign_key_definitions(
    connection: psycopg.Connection, child_table: str, parent_table: str
) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_constraintdef(con.oid) "
            "FROM pg_constraint AS con "
            "JOIN pg_class AS child ON child.oid = con.conrelid "
            "JOIN pg_class AS parent ON parent.oid = con.confrelid "
            "WHERE con.contype = 'f' AND child.relname = %s "
            "AND parent.relname = %s AND child.relnamespace = current_schema()::regnamespace",
            (child_table, parent_table),
        )
        return [row[0] for row in cursor.fetchall()]


def test_time_fields_are_timezone_aware(test_connection: psycopg.Connection) -> None:
    with test_connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = current_schema()"
        )
        time_fields: dict[str, int] = {}
        for table, column, data_type in cursor.fetchall():
            if column.endswith(("_at", "_until")):
                assert data_type == "timestamp with time zone", f"{table}.{column}"
                time_fields[table] = time_fields.get(table, 0) + 1
    for table in EXPECTED_TABLES:
        assert time_fields.get(table, 0) >= 1, f"{table} needs a UTC-capable timestamp"


def test_schema_avoids_plaintext_credentials_and_content(
    test_connection: psycopg.Connection,
) -> None:
    with test_connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
        columns: dict[str, set[str]] = {}
        for table, column in cursor.fetchall():
            columns.setdefault(table, set()).add(column)

    assert "password_hash" in columns["users"]
    assert "token_hash" in columns["sessions"]
    forbidden = {
        "password",
        "plain_password",
        "session_token",
        "api_key",
        "secret_key",
        "raw_video",
        "video_body",
        "summary_body",
        "signed_url",
    }
    for table, names in columns.items():
        assert not forbidden.intersection(names), table


def test_unique_email_and_credit_range_are_enforced(
    test_connection: psycopg.Connection,
) -> None:
    email = f"s302-{uuid4()}@example.invalid"
    with pytest.raises(psycopg.errors.UniqueViolation):
        with test_connection.transaction():
            test_connection.execute(
                "INSERT INTO users (email_normalized, password_hash) VALUES (%s, %s)",
                (email, "test-hash-placeholder"),
            )
            test_connection.execute(
                "INSERT INTO users (email_normalized, password_hash) VALUES (%s, %s)",
                (email, "test-hash-placeholder"),
            )
    with pytest.raises(psycopg.errors.CheckViolation):
        with test_connection.transaction():
            test_connection.execute(
                "INSERT INTO users (email_normalized, password_hash, credits_available) "
                "VALUES (%s, %s, 4)",
                (f"s302-{uuid4()}@example.invalid", "test-hash-placeholder"),
            )


def test_cross_user_retry_and_duplicate_quota_event_are_rejected(
    test_connection: psycopg.Connection,
) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with test_connection.transaction():
            first = test_connection.execute(
                "INSERT INTO users (email_normalized, password_hash) VALUES (%s, %s) RETURNING id",
                (f"s302-{uuid4()}@example.invalid", "test-hash-placeholder"),
            ).fetchone()[0]
            second = test_connection.execute(
                "INSERT INTO users (email_normalized, password_hash) VALUES (%s, %s) RETURNING id",
                (f"s302-{uuid4()}@example.invalid", "test-hash-placeholder"),
            ).fetchone()[0]
            task = test_connection.execute(
                "INSERT INTO tasks (user_id, input_kind) VALUES (%s, 'LOCAL') RETURNING id",
                (first,),
            ).fetchone()[0]
            test_connection.execute(
                "INSERT INTO retry_events (task_id, user_id, request_key, source) "
                "VALUES (%s, %s, %s, 'USER')",
                (task, second, f"s302-{uuid4()}"),
            )

    with pytest.raises(psycopg.errors.UniqueViolation):
        with test_connection.transaction():
            user = test_connection.execute(
                "INSERT INTO users (email_normalized, password_hash) VALUES (%s, %s) RETURNING id",
                (f"s302-{uuid4()}@example.invalid", "test-hash-placeholder"),
            ).fetchone()[0]
            key = f"s302-{uuid4()}"
            for _ in range(2):
                test_connection.execute(
                    "INSERT INTO quota_events (user_id, event_key, event_type, delta) "
                    "VALUES (%s, %s, 'RECOVER', 1)",
                    (user, key),
                )


def test_one_spend_and_refund_per_task_even_with_different_keys(
    test_connection: psycopg.Connection,
) -> None:
    user = test_connection.execute(
        "INSERT INTO users (email_normalized, password_hash) VALUES (%s, %s) RETURNING id",
        (f"s302-{uuid4()}@example.invalid", "test-hash-placeholder"),
    ).fetchone()[0]
    task = test_connection.execute(
        "INSERT INTO tasks (user_id, input_kind) VALUES (%s, 'LOCAL') RETURNING id",
        (user,),
    ).fetchone()[0]
    for event_type, delta in (("SPEND", -1), ("REFUND", 1)):
        test_connection.execute(
            "INSERT INTO quota_events (user_id, task_id, event_key, event_type, delta) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user, task, str(uuid4()), event_type, delta),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            with test_connection.transaction():
                test_connection.execute(
                    "INSERT INTO quota_events "
                    "(user_id, task_id, event_key, event_type, delta) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (user, task, str(uuid4()), event_type, delta),
                )


def test_evaluation_asset_cannot_be_linked_to_another_task(
    test_connection: psycopg.Connection,
) -> None:
    users = []
    tasks = []
    for _ in range(2):
        user = test_connection.execute(
            "INSERT INTO users (email_normalized, password_hash) VALUES (%s, %s) RETURNING id",
            (f"s302-{uuid4()}@example.invalid", "test-hash-placeholder"),
        ).fetchone()[0]
        task = test_connection.execute(
            "INSERT INTO tasks (user_id, input_kind) VALUES (%s, 'LOCAL') RETURNING id",
            (user,),
        ).fetchone()[0]
        users.append(user)
        tasks.append(task)
    asset = test_connection.execute(
        "INSERT INTO media_assets (task_id, user_id, asset_kind, storage_key) "
        "VALUES (%s, %s, 'EVALUATION', %s) RETURNING id",
        (tasks[0], users[0], f"s302/{uuid4()}"),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with test_connection.transaction():
            test_connection.execute(
                "INSERT INTO evaluation_links (task_id, user_id, asset_id, link_kind) "
                "VALUES (%s, %s, %s, 'ANNOTATION')",
                (tasks[1], users[1], asset),
            )


def test_deletion_audit_can_outlive_task_metadata(
    test_connection: psycopg.Connection,
) -> None:
    user = test_connection.execute(
        "INSERT INTO users (email_normalized, password_hash) VALUES (%s, %s) RETURNING id",
        (f"s302-{uuid4()}@example.invalid", "test-hash-placeholder"),
    ).fetchone()[0]
    task = test_connection.execute(
        "INSERT INTO tasks (user_id, input_kind) VALUES (%s, 'LOCAL') RETURNING id",
        (user,),
    ).fetchone()[0]
    audit = test_connection.execute(
        "INSERT INTO audit_events "
        "(target_user_id, task_id, event_type, result, retention_class) "
        "VALUES (%s, %s, 'DELETE', 'RECORDED', 'DELETION_90D') RETURNING id",
        (user, task),
    ).fetchone()[0]
    test_connection.execute("DELETE FROM tasks WHERE id = %s", (task,))
    stored_task = test_connection.execute(
        "SELECT task_id FROM audit_events WHERE id = %s", (audit,)
    ).fetchone()[0]
    assert stored_task == task
