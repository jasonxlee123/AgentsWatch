\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE security.api_key (
    api_key_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL
        REFERENCES security.user_account(user_id) ON DELETE RESTRICT,
    key_prefix text NOT NULL UNIQUE
        CHECK (key_prefix ~ '^awk_[0-9a-f]{16}$'),
    key_hash char(64) NOT NULL UNIQUE
        CHECK (key_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at timestamptz,
    CHECK (last_used_at IS NULL OR last_used_at >= created_at),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE UNIQUE INDEX uq_api_key_one_active_per_user
    ON security.api_key (user_id)
    WHERE revoked_at IS NULL;

CREATE INDEX ix_api_key_user_created
    ON security.api_key (user_id, created_at DESC);

COMMIT;
