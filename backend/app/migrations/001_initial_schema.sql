-- S3-02: metadata only. Media bodies and credentials are never stored here.
-- PostgreSQL TIMESTAMPTZ values are UTC instants; API presentation converts zones.

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email_normalized text NOT NULL UNIQUE CHECK (length(email_normalized) > 0),
    password_hash text NOT NULL CHECK (length(password_hash) > 0),
    role text NOT NULL DEFAULT 'USER' CHECK (role IN ('USER', 'ADMIN')),
    -- S3-05 ignores this bounded balance for ADMIN users (unlimited credits).
    credits_available smallint NOT NULL DEFAULT 3 CHECK (credits_available BETWEEN 0 AND 3),
    recovery_started_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id),
    token_hash text NOT NULL UNIQUE CHECK (length(token_hash) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CHECK (expires_at > created_at),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX sessions_user_active_idx ON sessions (user_id, expires_at)
    WHERE revoked_at IS NULL;

CREATE TABLE tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id),
    input_kind text NOT NULL CHECK (input_kind IN ('LOCAL', 'URL')),
    display_filename text,
    duration_seconds numeric(12, 3) CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    status text NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED', 'PROCESSING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    failure_category text CHECK (failure_category IN
        ('USER_INPUT', 'INTERNAL', 'EXTERNAL_TIMEOUT', 'EXTERNAL_RATE_LIMIT',
         'EXTERNAL_UNAVAILABLE', 'UNKNOWN', 'USER_CANCELLED')),
    failure_code text,
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    cooldown_level integer NOT NULL DEFAULT 0 CHECK (cooldown_level >= 0),
    cooldown_until timestamptz,
    next_retry_at timestamptz,
    lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    lease_owner text,
    lease_expires_at timestamptz,
    last_heartbeat_at timestamptz,
    deletion_requested_at timestamptz,
    deleted_at timestamptz,
    UNIQUE (id, user_id),
    CHECK (deleted_at IS NULL OR deletion_requested_at IS NOT NULL)
);
CREATE INDEX tasks_queue_idx ON tasks (submitted_at, user_id, id)
    WHERE status = 'QUEUED' AND deletion_requested_at IS NULL;
CREATE INDEX tasks_user_history_idx ON tasks (user_id, created_at DESC);
CREATE INDEX tasks_expired_lease_idx ON tasks (lease_expires_at)
    WHERE status = 'PROCESSING';
CREATE INDEX tasks_deletion_idx ON tasks (deletion_requested_at)
    WHERE deletion_requested_at IS NOT NULL AND deleted_at IS NULL;

CREATE TABLE task_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number >= 0),
    lease_generation bigint NOT NULL CHECK (lease_generation >= 0),
    status text NOT NULL CHECK (status IN ('PROCESSING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    failure_category text CHECK (failure_category IN
        ('USER_INPUT', 'INTERNAL', 'EXTERNAL_TIMEOUT', 'EXTERNAL_RATE_LIMIT',
         'EXTERNAL_UNAVAILABLE', 'UNKNOWN', 'USER_CANCELLED')),
    failure_code text,
    model_request_id text,
    FOREIGN KEY (task_id, user_id) REFERENCES tasks(id, user_id),
    UNIQUE (task_id, attempt_number),
    UNIQUE (task_id, lease_generation),
    UNIQUE (id, task_id, user_id),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX task_attempts_task_started_idx ON task_attempts (task_id, started_at);

CREATE TABLE quota_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id),
    task_id uuid,
    attempt_id uuid,
    event_key text NOT NULL UNIQUE CHECK (length(event_key) > 0),
    event_type text NOT NULL CHECK (event_type IN ('SPEND', 'REFUND', 'RECOVER')),
    delta smallint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (task_id, user_id) REFERENCES tasks(id, user_id),
    FOREIGN KEY (attempt_id, task_id, user_id)
        REFERENCES task_attempts(id, task_id, user_id),
    CHECK ((event_type = 'SPEND' AND delta = -1 AND task_id IS NOT NULL)
        OR (event_type = 'REFUND' AND delta = 1 AND task_id IS NOT NULL)
        OR (event_type = 'RECOVER' AND delta = 1 AND task_id IS NULL)),
    CHECK (attempt_id IS NULL OR task_id IS NOT NULL)
);
CREATE UNIQUE INDEX quota_once_spend_idx ON quota_events (task_id)
    WHERE event_type = 'SPEND';
CREATE UNIQUE INDEX quota_once_refund_idx ON quota_events (task_id)
    WHERE event_type = 'REFUND';
CREATE INDEX quota_events_user_time_idx ON quota_events (user_id, created_at);

CREATE TABLE retry_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    request_key text NOT NULL CHECK (length(request_key) > 0),
    source text NOT NULL CHECK (source IN ('USER', 'SYSTEM')),
    accepted_at timestamptz NOT NULL DEFAULT now(),
    cooldown_triggered boolean NOT NULL DEFAULT false,
    FOREIGN KEY (task_id, user_id) REFERENCES tasks(id, user_id),
    UNIQUE (task_id, request_key)
);
CREATE INDEX retry_events_window_idx ON retry_events (task_id, accepted_at DESC);

CREATE TABLE media_assets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    asset_kind text NOT NULL CHECK (length(asset_kind) > 0),
    storage_key text NOT NULL UNIQUE CHECK (length(storage_key) > 0),
    byte_size bigint CHECK (byte_size IS NULL OR byte_size >= 0),
    cleanup_state text NOT NULL DEFAULT 'REGISTERED'
        CHECK (cleanup_state IN ('REGISTERED', 'DELETING', 'DELETED', 'FAILED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    FOREIGN KEY (task_id, user_id) REFERENCES tasks(id, user_id),
    UNIQUE (id, task_id, user_id),
    CHECK (deleted_at IS NULL OR cleanup_state = 'DELETED')
);
CREATE INDEX media_assets_task_cleanup_idx ON media_assets (task_id, cleanup_state);

-- Each evaluation artifact has one source task; de-identification does not break
-- this controlled deletion link. The artifact itself remains a media_assets row.
CREATE TABLE evaluation_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    asset_id uuid NOT NULL UNIQUE,
    link_kind text NOT NULL CHECK (length(link_kind) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (task_id, user_id) REFERENCES tasks(id, user_id),
    FOREIGN KEY (asset_id, task_id, user_id)
        REFERENCES media_assets(id, task_id, user_id)
);
CREATE INDEX evaluation_links_task_idx ON evaluation_links (task_id);

CREATE TABLE task_consents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    purpose text NOT NULL CHECK (length(purpose) > 0),
    access_scope text NOT NULL CHECK (length(access_scope) > 0),
    granted_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    FOREIGN KEY (task_id, user_id) REFERENCES tasks(id, user_id),
    CHECK (revoked_at IS NULL OR revoked_at >= granted_at)
);
CREATE UNIQUE INDEX task_consents_one_active_idx
    ON task_consents (task_id, purpose, access_scope)
    WHERE revoked_at IS NULL;

-- Audit rows intentionally retain a task/user identifier snapshot without a
-- task FK: deletion audit can outlive task data for its separate 90-day term.
CREATE TABLE audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id uuid,
    target_user_id uuid NOT NULL,
    task_id uuid,
    event_type text NOT NULL CHECK (length(event_type) > 0),
    reason_code text,
    result text NOT NULL CHECK (length(result) > 0),
    retention_class text NOT NULL
        CHECK (retention_class IN ('TASK_30D', 'DELETION_90D')),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (actor_user_id) REFERENCES users(id),
    FOREIGN KEY (target_user_id) REFERENCES users(id)
);
CREATE INDEX audit_events_task_time_idx ON audit_events (task_id, created_at);
CREATE INDEX audit_events_retention_idx ON audit_events (retention_class, created_at);

CREATE TABLE cleanup_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL UNIQUE,
    user_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'MANUAL_REQUIRED', 'COMPLETED')),
    started_at timestamptz NOT NULL DEFAULT now(),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_attempt_at timestamptz,
    next_attempt_at timestamptz,
    completed_at timestamptz,
    backup_cleanup_state text NOT NULL DEFAULT 'PENDING'
        CHECK (backup_cleanup_state IN ('NOT_APPLICABLE', 'PENDING', 'COMPLETED', 'FAILED')),
    backup_completed_at timestamptz,
    last_error_code text,
    FOREIGN KEY (task_id, user_id) REFERENCES tasks(id, user_id),
    UNIQUE (id, task_id, user_id),
    CHECK (completed_at IS NULL OR status = 'COMPLETED'),
    CHECK (backup_completed_at IS NULL OR backup_cleanup_state = 'COMPLETED')
);
CREATE INDEX cleanup_jobs_due_idx ON cleanup_jobs (next_attempt_at)
    WHERE status IN ('PENDING', 'RUNNING');

CREATE TABLE cleanup_alerts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cleanup_job_id uuid NOT NULL,
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    alert_code text NOT NULL CHECK (length(alert_code) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    FOREIGN KEY (cleanup_job_id, task_id, user_id)
        REFERENCES cleanup_jobs(id, task_id, user_id),
    CHECK (resolved_at IS NULL OR resolved_at >= created_at)
);
CREATE UNIQUE INDEX cleanup_alerts_one_open_idx
    ON cleanup_alerts (cleanup_job_id, alert_code) WHERE resolved_at IS NULL;
CREATE INDEX cleanup_alerts_open_time_idx ON cleanup_alerts (created_at)
    WHERE resolved_at IS NULL;
