\set ON_ERROR_STOP on

BEGIN;

CREATE FUNCTION org.protect_master_record()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '% cannot be physically deleted; update its status instead',
            TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME
            USING ERRCODE = 'P0001';
    END IF;
    IF to_jsonb(NEW) -> TG_ARGV[0] IS DISTINCT FROM to_jsonb(OLD) -> TG_ARGV[0] THEN
        RAISE EXCEPTION 'immutable field %.% cannot be changed',
            TG_TABLE_NAME, TG_ARGV[0]
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_department_identity_guard
BEFORE UPDATE OR DELETE ON org.department
FOR EACH ROW EXECUTE FUNCTION org.protect_master_record('department_code');

CREATE TRIGGER trg_person_identity_guard
BEFORE UPDATE OR DELETE ON org.person
FOR EACH ROW EXECUTE FUNCTION org.protect_master_record('employee_no');

COMMIT;
