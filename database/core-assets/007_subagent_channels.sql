\set ON_ERROR_STOP on

BEGIN;

DROP TRIGGER trg_agent_channel_primary ON asset.agent_channel_configuration;
DROP FUNCTION asset.validate_primary_agent_channel();

COMMIT;
