\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM asset.agent_deployment deployment
        JOIN asset.agent_asset agent
          ON agent.agent_id = deployment.primary_agent_id
        WHERE agent.structure_role <> 'primary'
    ) THEN
        RAISE EXCEPTION
            'cannot enforce primary-Agent deployment integrity: an existing deployment references a Subagent'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE asset.agent_asset
    ADD CONSTRAINT uq_agent_identity_structure_role
    UNIQUE (agent_id, structure_role);

ALTER TABLE asset.agent_deployment
    ADD COLUMN primary_agent_structure_role asset.agent_structure_role
        NOT NULL DEFAULT 'primary',
    ADD CONSTRAINT ck_agent_deployment_primary_role
        CHECK (primary_agent_structure_role = 'primary');

ALTER TABLE asset.agent_deployment
    ADD CONSTRAINT fk_agent_deployment_primary_role
    FOREIGN KEY (primary_agent_id, primary_agent_structure_role)
    REFERENCES asset.agent_asset (agent_id, structure_role)
    ON UPDATE RESTRICT
    ON DELETE RESTRICT;

COMMENT ON COLUMN asset.agent_deployment.primary_agent_structure_role IS
    'Invariant discriminator fixed to primary; forms a concurrency-safe composite foreign key to AgentAsset.structure_role.';
COMMENT ON CONSTRAINT fk_agent_deployment_primary_role ON asset.agent_deployment IS
    'Guarantees every Deployment references a Primary Agent and prevents a referenced Primary Agent from becoming a Subagent; PostgreSQL foreign-key row locks serialize concurrent changes.';

COMMIT;
