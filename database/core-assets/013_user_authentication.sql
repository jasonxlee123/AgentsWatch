\set ON_ERROR_STOP on

BEGIN;

CREATE SCHEMA security;

CREATE TABLE security.user_account (
    user_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username text NOT NULL CHECK (
        username = btrim(username)
        AND username ~ '^[A-Za-z][A-Za-z0-9_.-]{2,63}$'
    ),
    display_name text NOT NULL CHECK (btrim(display_name) <> ''),
    password_hash text NOT NULL CHECK (password_hash LIKE 'scrypt$%'),
    role text NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    failed_login_attempts integer NOT NULL DEFAULT 0 CHECK (failed_login_attempts >= 0),
    locked_until timestamptz,
    password_changed_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz,
    created_by text NOT NULL CHECK (btrim(created_by) <> ''),
    updated_by text NOT NULL CHECK (btrim(updated_by) <> ''),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_user_account_username_ci
    ON security.user_account (lower(username));
CREATE INDEX ix_user_account_status_role
    ON security.user_account (status, role, username);

CREATE TRIGGER trg_user_account_touch
BEFORE UPDATE ON security.user_account
FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();

CREATE TABLE security.login_challenge (
    challenge_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    expected_offset integer NOT NULL CHECK (expected_offset BETWEEN 0 AND 238),
    client_fingerprint_hash char(64) NOT NULL
        CHECK (client_fingerprint_hash ~ '^[0-9a-f]{64}$'),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at)
);

CREATE INDEX ix_login_challenge_expiry
    ON security.login_challenge (expires_at)
    WHERE used_at IS NULL;

CREATE TABLE security.user_session (
    session_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES security.user_account(user_id) ON DELETE RESTRICT,
    token_hash char(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    csrf_token_hash char(64) NOT NULL CHECK (csrf_token_hash ~ '^[0-9a-f]{64}$'),
    client_fingerprint_hash char(64) NOT NULL
        CHECK (client_fingerprint_hash ~ '^[0-9a-f]{64}$'),
    expires_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at)
);

CREATE INDEX ix_user_session_active_token
    ON security.user_session (token_hash, expires_at)
    WHERE revoked_at IS NULL;
CREATE INDEX ix_user_session_user_created
    ON security.user_session (user_id, created_at DESC);

COMMIT;
