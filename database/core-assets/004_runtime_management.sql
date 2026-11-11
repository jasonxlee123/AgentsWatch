\set ON_ERROR_STOP on

BEGIN;

CREATE TYPE asset.runtime_management_driver AS ENUM ('none', 'systemd', 'launchd', 'mock');

ALTER TABLE asset.runtime_instance
    ADD COLUMN management_driver asset.runtime_management_driver NOT NULL DEFAULT 'none',
    ADD COLUMN management_target text,
    ADD COLUMN last_operation_at timestamptz,
    ADD CONSTRAINT ck_runtime_management_target CHECK (
        (management_driver = 'none' AND management_target IS NULL)
        OR
        (management_driver <> 'none' AND management_target IS NOT NULL AND btrim(management_target) <> '')
    ),
    ADD CONSTRAINT ck_runtime_management_mode CHECK (
        management_driver = 'none'
        OR (management_driver IN ('systemd', 'launchd', 'mock') AND deployment_mode = 'baremetal')
    );

ALTER TABLE automation.operation_run
    ADD COLUMN target_type text NOT NULL DEFAULT 'server'
        CHECK (target_type IN ('server', 'runtime', 'agent'));

ALTER TABLE automation.operation_host_result
    DROP CONSTRAINT operation_host_result_run_id_server_id_key,
    ADD COLUMN runtime_id uuid REFERENCES asset.runtime_instance(runtime_id) ON DELETE RESTRICT,
    ADD COLUMN target_key text;

UPDATE automation.operation_host_result SET target_key = server_id::text;

ALTER TABLE automation.operation_host_result
    ALTER COLUMN target_key SET NOT NULL,
    ADD CONSTRAINT uq_operation_run_target UNIQUE (run_id, target_key);

CREATE INDEX ix_operation_host_runtime ON automation.operation_host_result (runtime_id, started_at DESC)
    WHERE runtime_id IS NOT NULL;

COMMIT;
