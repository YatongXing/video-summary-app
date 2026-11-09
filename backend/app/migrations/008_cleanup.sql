ALTER TABLE cleanup_jobs ADD COLUMN worker_safe_after timestamptz;
ALTER TABLE cleanup_jobs ADD COLUMN last_error_kind text;
CREATE INDEX cleanup_jobs_user_idx ON cleanup_jobs(user_id,started_at DESC);
ALTER TABLE cleanup_jobs ADD COLUMN manual_requested boolean NOT NULL DEFAULT false;
