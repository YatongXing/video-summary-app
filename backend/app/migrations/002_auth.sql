ALTER TABLE users ADD COLUMN disabled_at timestamptz;
CREATE TABLE auth_limits (
    scope text NOT NULL,
    subject_hash text NOT NULL,
    window_started_at timestamptz NOT NULL DEFAULT now(),
    failure_count integer NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    blocked_until timestamptz,
    PRIMARY KEY (scope, subject_hash)
);
CREATE INDEX auth_limits_expiry_idx ON auth_limits (window_started_at);
