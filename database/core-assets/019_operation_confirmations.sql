\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE security.operation_confirmation (
    confirmation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL
        REFERENCES security.user_account(user_id) ON DELETE RESTRICT,
    request_method text NOT NULL CHECK (
        request_method = upper(request_method)
        AND request_method ~ '^[A-Z]+$'
        AND length(request_method) <= 16
    ),
    request_path text NOT NULL CHECK (
        request_path LIKE '/%'
        AND request_path !~ '[?#]'
        AND length(request_path) <= 2048
    ),
    query_hash char(64) NOT NULL CHECK (query_hash ~ '^[0-9a-f]{64}$'),
    body_hash char(64) NOT NULL CHECK (body_hash ~ '^[0-9a-f]{64}$'),
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at),
    CHECK (used_at IS NULL OR used_at >= created_at)
);

CREATE INDEX ix_operation_confirmation_active_user
    ON security.operation_confirmation (user_id, expires_at)
    WHERE used_at IS NULL;

CREATE INDEX ix_operation_confirmation_expiry
    ON security.operation_confirmation (expires_at);

COMMIT;
