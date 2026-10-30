CREATE TABLE schedule_events (
    id bigserial PRIMARY KEY,
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    submitted_at timestamptz NOT NULL,
    worker_id text NOT NULL,
    generation bigint NOT NULL,
    event_type text NOT NULL CHECK (event_type IN ('CLAIM','BLOCK','BYPASS','RECOVER')),
    reason_code text NOT NULL,
    related_task_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (task_id,user_id) REFERENCES tasks(id,user_id)
);
CREATE INDEX schedule_events_task_time_idx ON schedule_events(task_id,created_at);
-- Rows follow their source task's 30-day metadata retention, implemented by S3-09.
