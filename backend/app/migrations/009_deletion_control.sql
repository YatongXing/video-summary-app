-- Identity only: the independent deletion journal must survive database restore.
CREATE TABLE deletion_control_state (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 deployment_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
