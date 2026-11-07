-- CR-WP02 / L1.0: additive request structure only.
-- This does not activate request APIs, revoke legacy consent or change roles.
ALTER TABLE tasks
    ADD COLUMN support_version bigint NOT NULL DEFAULT 0
    CHECK (support_version >= 0);

CREATE TABLE support_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    scope text NOT NULL CHECK (scope IN ('summary', 'video', 'both')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- WP03 supplies both timestamps from one locked server-time sample.
    expires_at timestamptz NOT NULL,
    closed_at timestamptz,
    close_reason text,
    FOREIGN KEY (task_id, user_id)
        REFERENCES tasks(id, user_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT support_requests_uuid_v4 CHECK (
        substring(id::text FROM 15 FOR 1) = '4'
        AND substring(id::text FROM 20 FOR 1) IN ('8', '9', 'a', 'b')
    ),
    CONSTRAINT support_requests_fixed_lifetime CHECK (
        isfinite(created_at) AND isfinite(expires_at)
        AND expires_at - created_at = interval '86400 seconds'
    ),
    CONSTRAINT support_requests_closure CHECK (
        (closed_at IS NULL AND close_reason IS NULL)
        OR (
            closed_at IS NOT NULL AND isfinite(closed_at)
            AND closed_at >= created_at
            AND close_reason IS NOT NULL
            AND close_reason IN (
                'REVOKED', 'REPLACED', 'DISMISSED', 'TASK_DELETED', 'EXPIRED'
            )
        )
    )
);

-- Expired but not yet closed rows still occupy this slot. A new CREATE must
-- close that exact row in its source-task-locked transaction before inserting.
CREATE UNIQUE INDEX support_requests_one_open_idx
    ON support_requests (task_id) WHERE closed_at IS NULL;
CREATE INDEX support_requests_open_queue_idx
    ON support_requests (created_at, id) WHERE closed_at IS NULL;
CREATE INDEX support_requests_task_created_idx
    ON support_requests (task_id, created_at);

CREATE FUNCTION guard_support_request_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.task_id IS DISTINCT FROM OLD.task_id
        OR NEW.user_id IS DISTINCT FROM OLD.user_id
        OR NEW.scope IS DISTINCT FROM OLD.scope
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
        RAISE EXCEPTION 'Support request identity and grant fields are immutable'
            USING ERRCODE = '23514';
    END IF;

    IF OLD.closed_at IS NOT NULL AND (
        NEW.closed_at IS DISTINCT FROM OLD.closed_at
        OR NEW.close_reason IS DISTINCT FROM OLD.close_reason
    ) THEN
        RAISE EXCEPTION 'A closed support request cannot be changed or reopened'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER support_requests_guard_update
    BEFORE UPDATE ON support_requests
    FOR EACH ROW EXECUTE FUNCTION guard_support_request_update();

CREATE FUNCTION guard_task_support_version_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Existing task updates may leave the version unchanged. WP03 owns the
    -- request mutation and this +1 in the same transaction, not this trigger.
    IF NEW.support_version IS DISTINCT FROM OLD.support_version AND (
        NEW.support_version IS NULL
        OR NEW.support_version::numeric <> OLD.support_version::numeric + 1
    ) THEN
        RAISE EXCEPTION 'Support version must advance by exactly one'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tasks_guard_support_version_update
    BEFORE UPDATE OF support_version ON tasks
    FOR EACH ROW EXECUTE FUNCTION guard_task_support_version_update();
