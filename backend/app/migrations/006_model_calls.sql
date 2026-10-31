CREATE TABLE model_calls (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    lease_generation bigint NOT NULL,
    status text NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED')),
    model_version text NOT NULL,
    adapter_version text NOT NULL,
    prompt_version text NOT NULL,
    policy_version text NOT NULL,
    request_id text,
    input_tokens bigint CHECK (input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    cached_tokens bigint CHECK (cached_tokens >= 0),
    estimated_cost numeric CHECK (estimated_cost >= 0),
    currency text,
    error_category text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    FOREIGN KEY (attempt_id,task_id,user_id) REFERENCES task_attempts(id,task_id,user_id),
    UNIQUE (task_id,lease_generation)
);
CREATE INDEX model_calls_task_time_idx ON model_calls(task_id,created_at);
-- Metadata follows source task's 30-day term. Candidate content is not stored here.
