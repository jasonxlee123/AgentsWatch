\set ON_ERROR_STOP on

BEGIN;

CREATE TYPE asset.runtime_web_access_mode AS ENUM ('direct_url', 'local_ssh_tunnel', 'tailscale_serve');
CREATE TYPE asset.runtime_web_auth_mode AS ENUM ('none', 'token', 'password', 'username_password');
CREATE TYPE automation.web_access_session_status AS ENUM ('opening', 'active', 'failed', 'closed', 'expired');

ALTER TABLE asset.runtime_family
    ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE asset.runtime_web_endpoint (
    runtime_web_endpoint_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    runtime_id uuid NOT NULL REFERENCES asset.runtime_instance(runtime_id) ON DELETE RESTRICT,
    endpoint_code text NOT NULL DEFAULT 'control_ui' CHECK (btrim(endpoint_code) <> ''),
    display_name text NOT NULL DEFAULT 'WebUI' CHECK (btrim(display_name) <> ''),
    access_mode asset.runtime_web_access_mode NOT NULL,
    scheme text NOT NULL DEFAULT 'http' CHECK (scheme IN ('http', 'https')),
    direct_url text,
    remote_host text NOT NULL DEFAULT '127.0.0.1' CHECK (btrim(remote_host) <> ''),
    port_override integer CHECK (port_override BETWEEN 1 AND 65535),
    base_path text NOT NULL DEFAULT '/' CHECK (base_path LIKE '/%' AND base_path NOT LIKE '//%'),
    auth_mode asset.runtime_web_auth_mode NOT NULL DEFAULT 'none',
    encrypted_credential text,
    enabled boolean NOT NULL DEFAULT true,
    is_primary boolean NOT NULL DEFAULT true,
    last_opened_at timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_runtime_web_endpoint_code UNIQUE (runtime_id, endpoint_code),
    CONSTRAINT ck_runtime_web_endpoint_access CHECK (
        (access_mode IN ('direct_url', 'tailscale_serve') AND direct_url IS NOT NULL AND btrim(direct_url) <> '')
        OR
        (access_mode = 'local_ssh_tunnel' AND direct_url IS NULL)
    ),
    CONSTRAINT ck_runtime_web_endpoint_credential CHECK (
        (auth_mode = 'none' AND encrypted_credential IS NULL)
        OR
        (auth_mode <> 'none' AND encrypted_credential IS NOT NULL AND btrim(encrypted_credential) <> '')
    )
);

CREATE UNIQUE INDEX uq_runtime_one_primary_web_endpoint
    ON asset.runtime_web_endpoint (runtime_id)
    WHERE is_primary AND enabled;

CREATE TRIGGER trg_runtime_web_endpoint_touch
BEFORE UPDATE ON asset.runtime_web_endpoint
FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();

CREATE TABLE automation.runtime_web_access_session (
    access_session_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    runtime_web_endpoint_id uuid NOT NULL REFERENCES asset.runtime_web_endpoint(runtime_web_endpoint_id) ON DELETE RESTRICT,
    runtime_id uuid NOT NULL REFERENCES asset.runtime_instance(runtime_id) ON DELETE RESTRICT,
    requested_by text NOT NULL CHECK (btrim(requested_by) <> ''),
    access_mode asset.runtime_web_access_mode NOT NULL,
    status automation.web_access_session_status NOT NULL DEFAULT 'opening',
    local_port integer CHECK (local_port BETWEEN 1 AND 65535),
    process_id integer,
    failure_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    expires_at timestamptz NOT NULL,
    closed_at timestamptz,
    CHECK (expires_at > created_at),
    CHECK ((status IN ('closed', 'expired') AND closed_at IS NOT NULL) OR status NOT IN ('closed', 'expired'))
);

CREATE INDEX ix_runtime_web_session_runtime
    ON automation.runtime_web_access_session (runtime_id, created_at DESC);
CREATE INDEX ix_runtime_web_session_expiry
    ON automation.runtime_web_access_session (expires_at)
    WHERE status IN ('opening', 'active');

UPDATE asset.runtime_family
SET metadata = metadata || '{
  "web_ui": {
    "endpoint_code": "control_ui",
    "display_name": "OpenClaw Control UI",
    "default_base_path": "/",
    "websocket_required": true,
    "top_level_only": true,
    "token_delivery": "url_fragment"
  }
}'::jsonb
WHERE family_code = 'openclaw';

COMMIT;
