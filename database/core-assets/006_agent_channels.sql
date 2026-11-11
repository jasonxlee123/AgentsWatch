\set ON_ERROR_STOP on

BEGIN;

CREATE TYPE asset.agent_channel_type AS ENUM ('feishu');

CREATE TABLE asset.agent_channel_configuration (
    agent_channel_configuration_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL UNIQUE REFERENCES asset.agent_asset(agent_id) ON DELETE RESTRICT,
    channel_type asset.agent_channel_type NOT NULL,
    application_name text NOT NULL CHECK (btrim(application_name) <> ''),
    app_id text NOT NULL CHECK (btrim(app_id) <> ''),
    encrypted_app_secret text NOT NULL CHECK (btrim(encrypted_app_secret) <> ''),
    enabled boolean NOT NULL DEFAULT true,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_agent_channel_active_app_id
    ON asset.agent_channel_configuration (channel_type, app_id)
    WHERE enabled;

CREATE FUNCTION asset.validate_primary_agent_channel()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM asset.agent_asset
        WHERE agent_id = NEW.agent_id AND structure_role = 'primary'
    ) THEN
        RAISE EXCEPTION 'channel configuration is only allowed for a primary agent'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_agent_channel_primary
BEFORE INSERT OR UPDATE OF agent_id ON asset.agent_channel_configuration
FOR EACH ROW EXECUTE FUNCTION asset.validate_primary_agent_channel();

CREATE TRIGGER trg_agent_channel_touch
BEFORE UPDATE ON asset.agent_channel_configuration
FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();

COMMIT;
