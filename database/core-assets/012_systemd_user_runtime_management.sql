\set ON_ERROR_STOP on

BEGIN;

ALTER TABLE asset.runtime_instance
    DROP CONSTRAINT ck_runtime_management_mode,
    ADD COLUMN command_path text,
    ADD CONSTRAINT ck_runtime_command_path CHECK (
        command_path IS NULL
        OR (btrim(command_path) <> '' AND left(command_path, 1) = '/')
    ),
    ADD CONSTRAINT ck_runtime_management_mode CHECK (
        management_driver = 'none' OR deployment_mode = 'baremetal'
    );

-- PostgreSQL does not allow a newly-added enum value to be used until the
-- transaction commits. Keep this as the final statement in the migration.
ALTER TYPE asset.runtime_management_driver ADD VALUE IF NOT EXISTS 'systemd_user';

COMMIT;
