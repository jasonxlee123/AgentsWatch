\set ON_ERROR_STOP on

BEGIN;

CREATE TYPE asset.server_bootstrap_status AS ENUM (
    'not_required', 'pending', 'running', 'succeeded', 'failed'
);

ALTER TABLE asset.server_connection
    ADD COLUMN password_configured boolean NOT NULL DEFAULT false,
    ADD COLUMN ssh_host_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN ssh_host_key_fingerprints jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN bootstrap_status asset.server_bootstrap_status NOT NULL DEFAULT 'succeeded',
    ADD COLUMN bootstrap_error text,
    ADD COLUMN bootstrap_completed_at timestamptz,
    ADD COLUMN password_updated_at timestamptz,
    ADD CONSTRAINT ck_server_connection_host_keys CHECK (
        jsonb_typeof(ssh_host_keys) = 'array'
        AND jsonb_typeof(ssh_host_key_fingerprints) = 'array'
    );

CREATE TABLE security.server_ssh_credential (
    server_id uuid PRIMARY KEY REFERENCES asset.server_asset(server_id) ON DELETE RESTRICT,
    encrypted_password text NOT NULL CHECK (btrim(encrypted_password) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

UPDATE asset.server_connection
SET bootstrap_status = 'not_required'
WHERE connection_type = 'local';

COMMENT ON TABLE security.server_ssh_credential IS
    'Encrypted persistent SSH password isolated from read-only asset roles; used only for bootstrap/recovery';
COMMENT ON COLUMN asset.server_connection.ssh_host_keys IS
    'Administrator-confirmed OpenSSH known_hosts lines pinned for this connection';
COMMENT ON COLUMN asset.server_connection.bootstrap_status IS
    'Password bootstrap and key-only verification state';

COMMIT;
