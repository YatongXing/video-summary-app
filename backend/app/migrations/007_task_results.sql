-- Validated page and export bodies stay private in local PostgreSQL.
-- S3-09 clears these bodies alongside registered database assets.
CREATE TABLE task_results (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 task_id uuid NOT NULL,
 user_id uuid NOT NULL,
 language text NOT NULL CHECK(language IN ('zh','en')),
 lease_generation bigint NOT NULL,
 export_document jsonb NOT NULL,
 presentation jsonb NOT NULL,
 synthetic boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(task_id,user_id) REFERENCES tasks(id,user_id),
 UNIQUE(task_id,language),
 UNIQUE(id,task_id,user_id)
);
CREATE INDEX task_results_owner_idx ON task_results(user_id,task_id);
