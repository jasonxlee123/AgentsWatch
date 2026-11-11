\set ON_ERROR_STOP on

BEGIN;

CREATE SCHEMA automation;

CREATE TYPE asset.server_connection_type AS ENUM ('ssh', 'local');
CREATE TYPE asset.server_connection_status AS ENUM ('unknown', 'reachable', 'unreachable', 'auth_failed');
CREATE TYPE automation.run_status AS ENUM ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled');
CREATE TYPE automation.host_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'unreachable', 'skipped');

CREATE TABLE asset.server_connection (
    connection_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    server_id uuid NOT NULL UNIQUE REFERENCES asset.server_asset(server_id) ON DELETE RESTRICT,
    connection_type asset.server_connection_type NOT NULL DEFAULT 'ssh',
    host text NOT NULL CHECK (btrim(host) <> ''),
    port integer CHECK (port BETWEEN 1 AND 65535),
    username text,
    credential_ref text,
    use_become boolean NOT NULL DEFAULT false,
    connection_status asset.server_connection_status NOT NULL DEFAULT 'unknown',
    last_checked_at timestamptz,
    last_error text,
    last_facts jsonb,
    last_facts_at timestamptz,
    last_runtime_discovery jsonb,
    last_runtime_discovery_at timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_server_connection_fields CHECK (
        (connection_type = 'local' AND port IS NULL AND username IS NULL AND credential_ref IS NULL)
        OR
        (connection_type = 'ssh' AND port IS NOT NULL AND username IS NOT NULL AND btrim(username) <> '')
    )
);

CREATE TRIGGER trg_server_connection_touch
BEFORE UPDATE ON asset.server_connection
FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();

CREATE TABLE automation.operation_run (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_code text NOT NULL CHECK (btrim(operation_code) <> ''),
    status automation.run_status NOT NULL DEFAULT 'queued',
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    requested_by text NOT NULL CHECK (btrim(requested_by) <> ''),
    target_count integer NOT NULL CHECK (target_count > 0),
    succeeded_count integer NOT NULL DEFAULT 0 CHECK (succeeded_count >= 0),
    failed_count integer NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    playbook_version text NOT NULL CHECK (btrim(playbook_version) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    CHECK (succeeded_count + failed_count <= target_count)
);

CREATE TABLE automation.operation_host_result (
    host_result_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES automation.operation_run(run_id) ON DELETE RESTRICT,
    server_id uuid NOT NULL REFERENCES asset.server_asset(server_id) ON DELETE RESTRICT,
    status automation.host_status NOT NULL DEFAULT 'queued',
    result jsonb,
    error_message text,
    started_at timestamptz,
    finished_at timestamptz,
    UNIQUE (run_id, server_id)
);

CREATE INDEX ix_operation_run_created_at ON automation.operation_run (created_at DESC);
CREATE INDEX ix_operation_host_server ON automation.operation_host_result (server_id, started_at DESC);

COMMIT;
