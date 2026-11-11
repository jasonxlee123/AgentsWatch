\set ON_ERROR_STOP on

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA org;
CREATE SCHEMA asset;
CREATE SCHEMA audit;
CREATE SCHEMA ingest;

CREATE TYPE asset.environment_lifecycle AS ENUM ('planned', 'active', 'disabled', 'retired');
CREATE TYPE asset.cloud_provider_status AS ENUM ('active', 'disabled', 'retired');
CREATE TYPE asset.server_lifecycle AS ENUM ('planned', 'active', 'maintenance', 'disabled', 'retired');
CREATE TYPE asset.group_resource_type AS ENUM ('server', 'agent');
CREATE TYPE asset.group_lifecycle AS ENUM ('active', 'disabled', 'retired');
CREATE TYPE asset.runtime_role AS ENUM ('gateway');
CREATE TYPE asset.deployment_mode AS ENUM ('baremetal', 'container');
CREATE TYPE asset.runtime_lifecycle AS ENUM ('planned', 'active', 'disabled', 'retired');
CREATE TYPE asset.runtime_observed_status AS ENUM ('unknown', 'running', 'stopped', 'degraded', 'unreachable');
CREATE TYPE asset.agent_structure_role AS ENUM ('primary', 'subagent');
CREATE TYPE asset.agent_service_archetype AS ENUM ('personal', 'department', 'business');
CREATE TYPE asset.agent_lifecycle AS ENUM ('requested', 'testing', 'active', 'suspended', 'retired');
CREATE TYPE asset.agent_deployment_lifecycle AS ENUM ('planned', 'active', 'inactive', 'retired');
CREATE TYPE asset.deployment_member_mapping_status AS ENUM ('planned', 'verified', 'unresolved');
CREATE TYPE asset.discovery_status AS ENUM ('succeeded', 'partial', 'failed');
CREATE TYPE asset.network_target_type AS ENUM ('dns', 'ipv4', 'ipv6', 'cidr', 'url');
CREATE TYPE asset.network_protocol AS ENUM ('tcp', 'udp', 'http', 'https');
CREATE TYPE audit.change_operation AS ENUM ('create', 'update', 'retire', 'restore', 'link', 'unlink');
CREATE TYPE ingest.import_mode AS ENUM ('standard', 'initial_migration');
CREATE TYPE ingest.import_status AS ENUM (
    'uploaded', 'validating', 'ready', 'committing', 'committed', 'rejected', 'failed', 'superseded'
);
CREATE TYPE ingest.import_row_status AS ENUM (
    'new', 'matched', 'unchanged', 'changed', 'conflict', 'invalid', 'ignored', 'committed'
);
CREATE TYPE ingest.import_change_action AS ENUM ('set', 'clear', 'ignore', 'conflict');

CREATE FUNCTION asset.touch_current_record()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.row_version := OLD.row_version + 1;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

CREATE FUNCTION asset.bump_row_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.row_version := OLD.row_version + 1;
    RETURN NEW;
END;
$$;

CREATE FUNCTION asset.reject_immutable_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME
        USING ERRCODE = 'P0001';
END;
$$;

CREATE TABLE asset.environment (
    environment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    environment_code text NOT NULL UNIQUE CHECK (btrim(environment_code) <> ''),
    name text NOT NULL CHECK (btrim(name) <> ''),
    stage text NOT NULL CHECK (btrim(stage) <> ''),
    region text,
    operations_team text,
    lifecycle_status asset.environment_lifecycle NOT NULL DEFAULT 'planned',
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE org.department (
    department_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    department_code text NOT NULL UNIQUE CHECK (btrim(department_code) <> ''),
    name text NOT NULL CHECK (btrim(name) <> ''),
    parent_department_id uuid REFERENCES org.department(department_id) ON DELETE RESTRICT,
    is_active boolean NOT NULL DEFAULT true,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (parent_department_id IS NULL OR parent_department_id <> department_id)
);

CREATE TABLE org.person (
    person_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_no text NOT NULL UNIQUE CHECK (btrim(employee_no) <> ''),
    display_name text NOT NULL CHECK (btrim(display_name) <> ''),
    primary_department_id uuid REFERENCES org.department(department_id) ON DELETE RESTRICT,
    employment_status text NOT NULL DEFAULT 'active' CHECK (employment_status IN ('active', 'inactive', 'departed')),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE asset.cloud_provider (
    cloud_provider_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_code text NOT NULL UNIQUE CHECK (btrim(provider_code) <> ''),
    name text NOT NULL CHECK (btrim(name) <> ''),
    provider_kind text NOT NULL CHECK (provider_kind IN ('public_cloud', 'private_cloud', 'on_premises', 'other')),
    status asset.cloud_provider_status NOT NULL DEFAULT 'active',
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE asset.server_asset (
    server_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    environment_id uuid NOT NULL REFERENCES asset.environment(environment_id) ON DELETE RESTRICT,
    cloud_provider_id uuid REFERENCES asset.cloud_provider(cloud_provider_id) ON DELETE RESTRICT,
    provider_instance_id text,
    name text NOT NULL CHECK (btrim(name) <> ''),
    lifecycle_status asset.server_lifecycle NOT NULL DEFAULT 'planned',
    cpu_cores numeric(8, 2) CHECK (cpu_cores > 0),
    memory_gib numeric(12, 2) CHECK (memory_gib > 0),
    disk_gib numeric(14, 2) CHECK (disk_gib > 0),
    internal_ip inet,
    public_ip inet,
    os_name text,
    os_version text,
    architecture text,
    work_account text,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_server_provider_instance_current
    ON asset.server_asset (cloud_provider_id, provider_instance_id)
    WHERE provider_instance_id IS NOT NULL AND lifecycle_status <> 'retired';
CREATE UNIQUE INDEX uq_server_internal_ip_current
    ON asset.server_asset (environment_id, internal_ip)
    WHERE internal_ip IS NOT NULL AND lifecycle_status <> 'retired';
CREATE UNIQUE INDEX uq_server_public_ip_current
    ON asset.server_asset (environment_id, public_ip)
    WHERE public_ip IS NOT NULL AND lifecycle_status <> 'retired';

CREATE TABLE asset.runtime_family (
    runtime_family_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    family_code text NOT NULL UNIQUE CHECK (btrim(family_code) <> ''),
    name text NOT NULL CHECK (btrim(name) <> ''),
    status asset.cloud_provider_status NOT NULL DEFAULT 'active',
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE asset.runtime_instance (
    runtime_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    server_id uuid NOT NULL REFERENCES asset.server_asset(server_id) ON DELETE RESTRICT,
    runtime_family_id uuid NOT NULL REFERENCES asset.runtime_family(runtime_family_id) ON DELETE RESTRICT,
    name text NOT NULL CHECK (btrim(name) <> ''),
    runtime_role asset.runtime_role NOT NULL DEFAULT 'gateway',
    deployment_mode asset.deployment_mode NOT NULL,
    lifecycle_status asset.runtime_lifecycle NOT NULL DEFAULT 'planned',
    observed_status asset.runtime_observed_status NOT NULL DEFAULT 'unknown',
    bind_address text,
    endpoint_port integer CHECK (endpoint_port BETWEEN 1 AND 65535),
    runtime_account text,
    config_path text,
    state_path text,
    container_name text,
    image_ref text,
    desired_version text,
    last_discovered_at timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_runtime_deployment_mode_fields CHECK (
        (deployment_mode = 'baremetal' AND container_name IS NULL AND image_ref IS NULL)
        OR
        (
            deployment_mode = 'container'
            AND container_name IS NOT NULL
            AND image_ref IS NOT NULL
            AND btrim(container_name) <> ''
            AND btrim(image_ref) <> ''
        )
    )
);

CREATE TABLE asset.management_group (
    group_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_code text NOT NULL UNIQUE CHECK (btrim(group_code) <> ''),
    name text NOT NULL CHECK (btrim(name) <> ''),
    resource_type asset.group_resource_type NOT NULL,
    purpose text,
    lifecycle_status asset.group_lifecycle NOT NULL DEFAULT 'active',
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE asset.server_group_member (
    server_group_member_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id uuid NOT NULL REFERENCES asset.management_group(group_id) ON DELETE RESTRICT,
    server_id uuid NOT NULL REFERENCES asset.server_asset(server_id) ON DELETE RESTRICT,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX uq_server_group_member_current
    ON asset.server_group_member (group_id, server_id)
    WHERE valid_to IS NULL;

CREATE TABLE asset.agent_asset (
    agent_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (btrim(name) <> ''),
    structure_role asset.agent_structure_role NOT NULL,
    parent_agent_id uuid REFERENCES asset.agent_asset(agent_id) ON DELETE RESTRICT,
    service_archetype asset.agent_service_archetype NOT NULL,
    lifecycle_status asset.agent_lifecycle NOT NULL DEFAULT 'requested',
    owning_department_id uuid REFERENCES org.department(department_id) ON DELETE RESTRICT,
    business_admin_person_id uuid REFERENCES org.person(person_id) ON DELETE RESTRICT,
    it_admin_person_id uuid REFERENCES org.person(person_id) ON DELETE RESTRICT,
    primary_service_person_id uuid REFERENCES org.person(person_id) ON DELETE RESTRICT,
    primary_service_department_id uuid REFERENCES org.department(department_id) ON DELETE RESTRICT,
    business_domain text,
    business_scenario text,
    description text,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (parent_agent_id IS NULL OR parent_agent_id <> agent_id),
    CONSTRAINT ck_agent_role_and_service_fields CHECK (
        (
            structure_role = 'primary'
            AND parent_agent_id IS NULL
            AND (
                (
                    service_archetype = 'personal'
                    AND primary_service_person_id IS NOT NULL
                    AND primary_service_department_id IS NULL
                    AND business_domain IS NULL
                    AND business_scenario IS NULL
                )
                OR
                (
                    service_archetype = 'department'
                    AND primary_service_person_id IS NULL
                    AND primary_service_department_id IS NOT NULL
                    AND business_domain IS NULL
                    AND business_scenario IS NULL
                )
                OR
                (
                    service_archetype = 'business'
                    AND primary_service_person_id IS NULL
                    AND primary_service_department_id IS NULL
                    AND business_domain IS NOT NULL
                    AND business_scenario IS NOT NULL
                    AND btrim(business_domain) <> ''
                    AND btrim(business_scenario) <> ''
                )
            )
        )
        OR
        (
            structure_role = 'subagent'
            AND parent_agent_id IS NOT NULL
            AND business_admin_person_id IS NULL
            AND it_admin_person_id IS NULL
            AND primary_service_person_id IS NULL
            AND primary_service_department_id IS NULL
            AND business_domain IS NULL
            AND business_scenario IS NULL
        )
    ),
    CONSTRAINT ck_agent_active_admins CHECK (
        structure_role = 'subagent'
        OR lifecycle_status NOT IN ('testing', 'active')
        OR (business_admin_person_id IS NOT NULL AND it_admin_person_id IS NOT NULL)
    )
);

CREATE TABLE asset.agent_accessible_person (
    agent_accessible_person_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL REFERENCES asset.agent_asset(agent_id) ON DELETE RESTRICT,
    person_id uuid NOT NULL REFERENCES org.person(person_id) ON DELETE RESTRICT,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX uq_agent_accessible_person_current
    ON asset.agent_accessible_person (agent_id, person_id)
    WHERE valid_to IS NULL;

CREATE TABLE asset.agent_group_member (
    agent_group_member_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id uuid NOT NULL REFERENCES asset.management_group(group_id) ON DELETE RESTRICT,
    agent_id uuid NOT NULL REFERENCES asset.agent_asset(agent_id) ON DELETE RESTRICT,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX uq_agent_group_member_current
    ON asset.agent_group_member (group_id, agent_id)
    WHERE valid_to IS NULL;

CREATE TABLE asset.agent_deployment (
    agent_deployment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    primary_agent_id uuid NOT NULL REFERENCES asset.agent_asset(agent_id) ON DELETE RESTRICT,
    runtime_id uuid NOT NULL REFERENCES asset.runtime_instance(runtime_id) ON DELETE RESTRICT,
    lifecycle_status asset.agent_deployment_lifecycle NOT NULL DEFAULT 'planned',
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (agent_deployment_id, runtime_id),
    CONSTRAINT ck_agent_deployment_validity CHECK (
        (lifecycle_status IN ('planned', 'active') AND valid_to IS NULL)
        OR
        (lifecycle_status IN ('inactive', 'retired') AND valid_to IS NOT NULL AND valid_to > valid_from)
    )
);

CREATE UNIQUE INDEX uq_agent_one_active_deployment
    ON asset.agent_deployment (primary_agent_id)
    WHERE lifecycle_status = 'active';

CREATE TABLE asset.agent_deployment_member (
    deployment_member_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_deployment_id uuid NOT NULL,
    runtime_id uuid NOT NULL,
    agent_id uuid NOT NULL REFERENCES asset.agent_asset(agent_id) ON DELETE RESTRICT,
    member_role asset.agent_structure_role NOT NULL,
    parent_member_id uuid,
    runtime_agent_key text,
    mapping_status asset.deployment_member_mapping_status NOT NULL DEFAULT 'planned',
    config_ref text,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (deployment_member_id, agent_deployment_id),
    FOREIGN KEY (agent_deployment_id, runtime_id)
        REFERENCES asset.agent_deployment(agent_deployment_id, runtime_id) ON DELETE RESTRICT,
    FOREIGN KEY (parent_member_id, agent_deployment_id)
        REFERENCES asset.agent_deployment_member(deployment_member_id, agent_deployment_id) ON DELETE RESTRICT,
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (
        (member_role = 'primary' AND parent_member_id IS NULL)
        OR
        (member_role = 'subagent' AND parent_member_id IS NOT NULL)
    ),
    CHECK (
        mapping_status <> 'verified'
        OR (
            runtime_agent_key IS NOT NULL
            AND btrim(runtime_agent_key) <> ''
        )
    )
);

CREATE UNIQUE INDEX uq_deployment_primary_member_current
    ON asset.agent_deployment_member (agent_deployment_id)
    WHERE valid_to IS NULL AND member_role = 'primary';
CREATE UNIQUE INDEX uq_deployment_agent_member_current
    ON asset.agent_deployment_member (agent_deployment_id, agent_id)
    WHERE valid_to IS NULL;
CREATE UNIQUE INDEX uq_runtime_agent_key_current
    ON asset.agent_deployment_member (runtime_id, runtime_agent_key)
    WHERE valid_to IS NULL AND runtime_agent_key IS NOT NULL;

CREATE TABLE asset.runtime_discovery_snapshot (
    snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    runtime_id uuid NOT NULL REFERENCES asset.runtime_instance(runtime_id) ON DELETE RESTRICT,
    discovered_at timestamptz NOT NULL,
    discovery_status asset.discovery_status NOT NULL,
    discovery_source text NOT NULL CHECK (btrim(discovery_source) <> ''),
    actual_version text,
    observed_agent_count integer CHECK (observed_agent_count >= 0),
    agent_count_confidence text NOT NULL DEFAULT 'verified' CHECK (
        agent_count_confidence IN ('verified', 'reported', 'legacy')
    ),
    content_hash char(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    extension_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    config_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_payload jsonb NOT NULL,
    error_code text,
    error_summary text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (discovery_status = 'failed' AND error_code IS NOT NULL)
        OR discovery_status <> 'failed'
    )
);

CREATE INDEX ix_runtime_snapshot_latest
    ON asset.runtime_discovery_snapshot (runtime_id, discovered_at DESC);

CREATE TABLE asset.agent_network_requirement (
    network_requirement_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL REFERENCES asset.agent_asset(agent_id) ON DELETE RESTRICT,
    system_name text NOT NULL CHECK (btrim(system_name) <> ''),
    target_type asset.network_target_type NOT NULL,
    target_value text NOT NULL CHECK (btrim(target_value) <> ''),
    protocol asset.network_protocol NOT NULL,
    port_from integer CHECK (port_from BETWEEN 1 AND 65535),
    port_to integer CHECK (port_to BETWEEN 1 AND 65535),
    purpose text,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (port_to IS NULL OR (port_from IS NOT NULL AND port_to >= port_from)),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE asset.external_identifier (
    external_identifier_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system text NOT NULL CHECK (btrim(source_system) <> ''),
    identifier_type text NOT NULL CHECK (btrim(identifier_type) <> ''),
    identifier_value text NOT NULL CHECK (btrim(identifier_value) <> ''),
    environment_id uuid REFERENCES asset.environment(environment_id) ON DELETE RESTRICT,
    cloud_provider_id uuid REFERENCES asset.cloud_provider(cloud_provider_id) ON DELETE RESTRICT,
    server_id uuid REFERENCES asset.server_asset(server_id) ON DELETE RESTRICT,
    group_id uuid REFERENCES asset.management_group(group_id) ON DELETE RESTRICT,
    runtime_id uuid REFERENCES asset.runtime_instance(runtime_id) ON DELETE RESTRICT,
    agent_id uuid REFERENCES asset.agent_asset(agent_id) ON DELETE RESTRICT,
    department_id uuid REFERENCES org.department(department_id) ON DELETE RESTRICT,
    person_id uuid REFERENCES org.person(person_id) ON DELETE RESTRICT,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (num_nonnulls(
        environment_id,
        cloud_provider_id,
        server_id,
        group_id,
        runtime_id,
        agent_id,
        department_id,
        person_id
    ) = 1)
);

CREATE UNIQUE INDEX uq_external_identifier_current
    ON asset.external_identifier (source_system, identifier_type, identifier_value)
    WHERE valid_to IS NULL;

CREATE TABLE audit.change_log (
    change_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_type text NOT NULL CHECK (actor_type IN ('user', 'import', 'migration', 'system')),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    source text NOT NULL CHECK (source IN ('ui', 'excel', 'migration', 'discovery', 'system')),
    object_type text NOT NULL CHECK (btrim(object_type) <> ''),
    object_id uuid NOT NULL,
    operation audit.change_operation NOT NULL,
    changed_fields text[] NOT NULL DEFAULT '{}',
    before_value jsonb,
    after_value jsonb,
    import_batch_id uuid,
    reason text,
    correlation_id uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE TABLE ingest.import_batch (
    import_batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    file_name text NOT NULL CHECK (btrim(file_name) <> ''),
    file_sha256 char(64) NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    template_version text NOT NULL CHECK (btrim(template_version) <> ''),
    import_mode ingest.import_mode NOT NULL DEFAULT 'standard',
    status ingest.import_status NOT NULL DEFAULT 'uploaded',
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    committed_at timestamptz
);

CREATE TABLE ingest.import_row (
    import_row_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    import_batch_id uuid NOT NULL REFERENCES ingest.import_batch(import_batch_id) ON DELETE RESTRICT,
    sheet_name text NOT NULL CHECK (btrim(sheet_name) <> ''),
    row_number integer NOT NULL CHECK (row_number >= 2),
    target_object_type text,
    target_object_id uuid,
    expected_row_version bigint CHECK (expected_row_version >= 1),
    raw_payload jsonb NOT NULL,
    normalized_payload jsonb NOT NULL,
    status ingest.import_row_status NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (import_batch_id, sheet_name, row_number)
);

CREATE TABLE ingest.import_issue (
    import_issue_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    import_batch_id uuid NOT NULL REFERENCES ingest.import_batch(import_batch_id) ON DELETE RESTRICT,
    import_row_id uuid REFERENCES ingest.import_row(import_row_id) ON DELETE RESTRICT,
    severity text NOT NULL CHECK (severity IN ('warning', 'error')),
    issue_code text NOT NULL CHECK (btrim(issue_code) <> ''),
    field_path text,
    message text NOT NULL CHECK (btrim(message) <> ''),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ingest.import_change (
    import_change_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    import_row_id uuid NOT NULL REFERENCES ingest.import_row(import_row_id) ON DELETE RESTRICT,
    field_path text NOT NULL CHECK (btrim(field_path) <> ''),
    action ingest.import_change_action NOT NULL,
    before_value jsonb,
    after_value jsonb,
    selected boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (import_row_id, field_path)
);

ALTER TABLE audit.change_log
    ADD CONSTRAINT fk_change_log_import_batch
    FOREIGN KEY (import_batch_id) REFERENCES ingest.import_batch(import_batch_id) ON DELETE RESTRICT;

CREATE TRIGGER trg_environment_touch BEFORE UPDATE ON asset.environment
    FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();
CREATE TRIGGER trg_department_touch BEFORE UPDATE ON org.department
    FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();
CREATE TRIGGER trg_person_touch BEFORE UPDATE ON org.person
    FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();
CREATE TRIGGER trg_cloud_provider_touch BEFORE UPDATE ON asset.cloud_provider
    FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();
CREATE TRIGGER trg_server_touch BEFORE UPDATE ON asset.server_asset
    FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();
CREATE TRIGGER trg_runtime_family_touch BEFORE UPDATE ON asset.runtime_family
    FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();
CREATE TRIGGER trg_runtime_touch BEFORE UPDATE ON asset.runtime_instance
    FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();
CREATE TRIGGER trg_group_touch BEFORE UPDATE ON asset.management_group
    FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();
CREATE TRIGGER trg_agent_touch BEFORE UPDATE ON asset.agent_asset
    FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();
CREATE TRIGGER trg_deployment_touch BEFORE UPDATE ON asset.agent_deployment
    FOR EACH ROW EXECUTE FUNCTION asset.touch_current_record();

CREATE TRIGGER trg_server_group_member_version BEFORE UPDATE ON asset.server_group_member
    FOR EACH ROW EXECUTE FUNCTION asset.bump_row_version();
CREATE TRIGGER trg_agent_access_version BEFORE UPDATE ON asset.agent_accessible_person
    FOR EACH ROW EXECUTE FUNCTION asset.bump_row_version();
CREATE TRIGGER trg_agent_group_member_version BEFORE UPDATE ON asset.agent_group_member
    FOR EACH ROW EXECUTE FUNCTION asset.bump_row_version();
CREATE TRIGGER trg_deployment_member_version BEFORE UPDATE ON asset.agent_deployment_member
    FOR EACH ROW EXECUTE FUNCTION asset.bump_row_version();
CREATE TRIGGER trg_network_requirement_version BEFORE UPDATE ON asset.agent_network_requirement
    FOR EACH ROW EXECUTE FUNCTION asset.bump_row_version();
CREATE TRIGGER trg_external_identifier_version BEFORE UPDATE ON asset.external_identifier
    FOR EACH ROW EXECUTE FUNCTION asset.bump_row_version();

CREATE TRIGGER trg_snapshot_immutable
    BEFORE UPDATE OR DELETE ON asset.runtime_discovery_snapshot
    FOR EACH ROW EXECUTE FUNCTION asset.reject_immutable_mutation();
CREATE TRIGGER trg_change_log_immutable
    BEFORE UPDATE OR DELETE ON audit.change_log
    FOR EACH ROW EXECUTE FUNCTION asset.reject_immutable_mutation();

COMMIT;
