CREATE TABLE input_imports (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id),
    object_key text NOT NULL UNIQUE,
    state text NOT NULL DEFAULT 'STAGING' CHECK (state IN ('STAGING','STORING','CLEANUP_REQUIRED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '6 hours'),
    cleanup_error_code text
);
CREATE INDEX input_imports_expiry_idx ON input_imports(expires_at);
ALTER TABLE media_assets ADD COLUMN has_audio boolean;
