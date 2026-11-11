\set ON_ERROR_STOP on

BEGIN;

ALTER TABLE automation.operation_host_result
    ADD COLUMN target_plan jsonb,
    ADD CONSTRAINT ck_operation_host_target_plan
        CHECK (target_plan IS NULL OR jsonb_typeof(target_plan) = 'object');

CREATE TABLE automation.operation_event (
    operation_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES automation.operation_run(run_id) ON DELETE RESTRICT,
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    phase text NOT NULL CHECK (phase IN ('dispatch', 'preflight', 'ansible', 'finalize')),
    event_type text NOT NULL CHECK (btrim(event_type) <> ''),
    event_level text NOT NULL DEFAULT 'info' CHECK (event_level IN ('info', 'warning', 'error')),
    target_key text,
    play_name text,
    task_name text,
    task_action text,
    changed boolean,
    stdout text,
    event_data jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(event_data) = 'object'),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence_no),
    CHECK (stdout IS NULL OR octet_length(stdout) <= 16384)
);

CREATE INDEX ix_operation_event_run_sequence
    ON automation.operation_event (run_id, sequence_no);

CREATE INDEX ix_operation_event_run_target
    ON automation.operation_event (run_id, target_key, sequence_no)
    WHERE target_key IS NOT NULL;

CREATE FUNCTION automation.prevent_operation_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'operation events are immutable' USING ERRCODE = '23514';
END;
$function$;

CREATE TRIGGER trg_operation_event_immutable
BEFORE UPDATE OR DELETE ON automation.operation_event
FOR EACH ROW EXECUTE FUNCTION automation.prevent_operation_event_mutation();

COMMIT;
