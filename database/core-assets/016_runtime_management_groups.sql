\set ON_ERROR_STOP on

BEGIN;

ALTER TYPE asset.group_resource_type ADD VALUE IF NOT EXISTS 'runtime';

CREATE TABLE asset.runtime_group_member (
    runtime_group_member_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id uuid NOT NULL REFERENCES asset.management_group(group_id) ON DELETE RESTRICT,
    runtime_id uuid NOT NULL REFERENCES asset.runtime_instance(runtime_id) ON DELETE RESTRICT,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX uq_runtime_group_member_current
    ON asset.runtime_group_member (group_id, runtime_id)
    WHERE valid_to IS NULL;

CREATE INDEX ix_runtime_group_member_runtime_current
    ON asset.runtime_group_member (runtime_id, group_id)
    WHERE valid_to IS NULL;

CREATE TRIGGER trg_runtime_group_member_version
BEFORE UPDATE ON asset.runtime_group_member
FOR EACH ROW EXECUTE FUNCTION asset.bump_row_version();

COMMIT;
