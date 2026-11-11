\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE automation.automation_definition (
    automation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE CHECK (code ~ '^[a-z][a-z0-9_.-]{2,99}$'),
    name text NOT NULL CHECK (btrim(name) <> ''),
    description text NOT NULL DEFAULT '',
    category text NOT NULL DEFAULT '未分类' CHECK (btrim(category) <> ''),
    scope text NOT NULL CHECK (scope IN ('server', 'runtime', 'agent')),
    lifecycle_status text NOT NULL DEFAULT 'draft'
        CHECK (lifecycle_status IN ('draft', 'published', 'disabled', 'archived')),
    risk_level text NOT NULL DEFAULT 'low' CHECK (risk_level IN ('low', 'medium', 'high')),
    requires_become boolean NOT NULL DEFAULT false,
    supports_batch boolean NOT NULL DEFAULT false,
    timeout_seconds integer NOT NULL DEFAULT 120 CHECK (timeout_seconds BETWEEN 1 AND 3600),
    builtin boolean NOT NULL DEFAULT false,
    current_published_version_id uuid,
    created_by text NOT NULL CHECK (btrim(created_by) <> ''),
    updated_by text NOT NULL CHECK (btrim(updated_by) <> ''),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_automation_definition_touch
BEFORE UPDATE ON automation.automation_definition
FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();

CREATE TABLE automation.automation_version (
    automation_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    automation_id uuid NOT NULL REFERENCES automation.automation_definition(automation_id) ON DELETE RESTRICT,
    version_label text NOT NULL CHECK (btrim(version_label) <> ''),
    version_status text NOT NULL DEFAULT 'draft'
        CHECK (version_status IN ('draft', 'published', 'withdrawn')),
    source_kind text NOT NULL DEFAULT 'managed' CHECK (source_kind IN ('builtin', 'managed')),
    playbook_path text,
    playbook_content text NOT NULL CHECK (btrim(playbook_content) <> ''),
    source_paths jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(source_paths) = 'array'),
    parameter_schema jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(parameter_schema) = 'object'),
    content_hash char(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    validation_status text NOT NULL DEFAULT 'pending'
        CHECK (validation_status IN ('pending', 'passed', 'failed')),
    validation_report jsonb,
    validated_at timestamptz,
    created_by text NOT NULL CHECK (btrim(created_by) <> ''),
    published_by text,
    published_at timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (automation_id, version_label),
    CHECK (
        (version_status = 'published' AND published_by IS NOT NULL AND published_at IS NOT NULL)
        OR version_status <> 'published'
    )
);

CREATE FUNCTION automation.prevent_published_version_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' AND OLD.version_status = 'published' THEN
        RAISE EXCEPTION 'published automation versions are immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.version_status = 'published' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'published automation versions are immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER trg_automation_version_00_immutable
BEFORE UPDATE OR DELETE ON automation.automation_version
FOR EACH ROW EXECUTE FUNCTION automation.prevent_published_version_mutation();

CREATE TRIGGER trg_automation_version_touch
BEFORE UPDATE ON automation.automation_version
FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();

CREATE UNIQUE INDEX uq_automation_one_draft_version
    ON automation.automation_version (automation_id)
    WHERE version_status = 'draft';

ALTER TABLE automation.automation_definition
    ADD CONSTRAINT fk_automation_current_published_version
    FOREIGN KEY (current_published_version_id)
    REFERENCES automation.automation_version(automation_version_id)
    ON DELETE RESTRICT;

ALTER TABLE automation.operation_run
    ADD COLUMN automation_version_id uuid
        REFERENCES automation.automation_version(automation_version_id) ON DELETE RESTRICT,
    ADD COLUMN retry_of_run_id uuid
        REFERENCES automation.operation_run(run_id) ON DELETE RESTRICT,
    ADD COLUMN cancellation_requested_at timestamptz,
    ADD COLUMN cancellation_requested_by text;

CREATE INDEX ix_automation_definition_scope_status
    ON automation.automation_definition (scope, lifecycle_status, name);
CREATE INDEX ix_automation_version_history
    ON automation.automation_version (automation_id, created_at DESC);
CREATE INDEX ix_operation_run_status_created
    ON automation.operation_run (status, created_at DESC);
CREATE INDEX ix_operation_run_operation_created
    ON automation.operation_run (operation_code, created_at DESC);

COMMIT;
