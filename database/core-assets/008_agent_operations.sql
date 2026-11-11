\set ON_ERROR_STOP on

BEGIN;

ALTER TABLE automation.operation_host_result
    ADD COLUMN agent_id uuid REFERENCES asset.agent_asset(agent_id) ON DELETE RESTRICT,
    ADD COLUMN agent_deployment_id uuid REFERENCES asset.agent_deployment(agent_deployment_id) ON DELETE RESTRICT;

CREATE INDEX ix_operation_host_agent
    ON automation.operation_host_result (agent_id, started_at DESC)
    WHERE agent_id IS NOT NULL;

COMMIT;
