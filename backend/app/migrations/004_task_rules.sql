ALTER TABLE tasks ADD COLUMN timeout_retry_count integer NOT NULL DEFAULT 0 CHECK (timeout_retry_count >= 0);
