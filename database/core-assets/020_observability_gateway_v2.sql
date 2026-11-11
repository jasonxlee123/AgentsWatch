\set ON_ERROR_STOP on

BEGIN;

CREATE VIEW integration.observability_gateway_v2 AS
WITH gateway_projection AS (
    SELECT
        runtime.runtime_id,
        gateway.identifier_value AS gateway_id,
        gateway.source_system AS gateway_id_source,
        gateway.identifier_type AS gateway_id_type,
        server.server_id,
        COALESCE(NULLIF(btrim(server.provider_instance_id), ''), server.name) AS server_display_id,
        deployment.agent_deployment_id,
        agent.agent_id AS primary_agent_id,
        agent.name AS lobster_name,
        department.department_code,
        department.name AS department_name,
        runtime.name AS runtime_name,
        family.family_code AS runtime_family,
        host(server.internal_ip) AS monitor_host,
        runtime.endpoint_port AS monitor_port,
        runtime.deployment_mode::text AS deployment_mode,
        runtime.bind_address,
        runtime.runtime_account,
        runtime.config_path,
        runtime.container_name,
        runtime.lifecycle_status::text AS lifecycle_status,
        environment.lifecycle_status::text AS environment_lifecycle_status,
        server.lifecycle_status::text AS server_lifecycle_status,
        runtime.lifecycle_status::text AS runtime_lifecycle_status,
        runtime.observed_status::text AS runtime_observed_status,
        deployment.lifecycle_status::text AS agent_deployment_lifecycle_status,
        agent.lifecycle_status::text AS primary_agent_lifecycle_status,
        environment.environment_code,
        environment.stage AS environment_stage,
        environment.region,
        environment.operations_team,
        runtime.desired_version,
        snapshot.actual_version,
        snapshot.snapshot_id AS discovery_snapshot_id,
        snapshot.discovery_status::text AS discovery_status,
        snapshot.discovered_at,
        snapshot.content_hash AS discovery_content_hash,
        CASE
            WHEN agent.agent_id IS NULL THEN NULL
            ELSE agent.name || '-' || COALESCE(NULLIF(btrim(server.provider_instance_id), ''), server.name)
        END AS display_name,
        (
            environment.lifecycle_status = 'active'
            AND server.lifecycle_status IN ('active', 'maintenance')
            AND runtime.lifecycle_status = 'active'
        ) AS monitoring_enabled,
        (
            gateway.identifier_value IS NOT NULL
            AND agent.agent_id IS NOT NULL
            AND server.internal_ip IS NOT NULL
            AND runtime.endpoint_port IS NOT NULL
            AND environment.lifecycle_status = 'active'
            AND server.lifecycle_status IN ('active', 'maintenance')
            AND runtime.lifecycle_status = 'active'
        ) AS monitoring_ready,
        (
            gateway.identifier_value IS NOT NULL
            AND deployment.agent_deployment_id IS NOT NULL
            AND agent.agent_id IS NOT NULL
        ) AS identity_ready,
        (
            environment.lifecycle_status = 'active'
            AND server.lifecycle_status IN ('active', 'maintenance')
            AND runtime.lifecycle_status = 'active'
            AND COALESCE(agent.lifecycle_status = 'active', false)
        ) AS lifecycle_ready,
        CASE runtime.deployment_mode
            WHEN 'baremetal' THEN
                NULLIF(btrim(runtime.runtime_account), '') IS NOT NULL
                AND NULLIF(btrim(runtime.config_path), '') IS NOT NULL
            WHEN 'container' THEN NULLIF(btrim(runtime.container_name), '') IS NOT NULL
            ELSE false
        END AS otel_configuration_ready,
        (
            server.internal_ip IS NOT NULL
            AND runtime.endpoint_port IS NOT NULL
        ) AS prometheus_target_ready,
        ARRAY_REMOVE(ARRAY[
            CASE WHEN gateway.identifier_value IS NULL THEN 'gateway_id_missing' END,
            CASE WHEN deployment.agent_deployment_id IS NULL THEN 'active_agent_deployment_missing' END,
            CASE WHEN agent.agent_id IS NULL THEN 'primary_agent_missing' END,
            CASE WHEN environment.lifecycle_status <> 'active' THEN 'environment_not_active' END,
            CASE
                WHEN server.lifecycle_status NOT IN ('active', 'maintenance')
                THEN 'server_lifecycle_not_monitorable'
            END,
            CASE WHEN runtime.lifecycle_status <> 'active' THEN 'runtime_not_active' END,
            CASE
                WHEN agent.agent_id IS NOT NULL AND agent.lifecycle_status <> 'active'
                THEN 'primary_agent_not_active'
            END,
            CASE
                WHEN runtime.deployment_mode = 'baremetal'
                 AND NULLIF(btrim(runtime.runtime_account), '') IS NULL
                THEN 'otel_runtime_account_missing'
            END,
            CASE
                WHEN runtime.deployment_mode = 'baremetal'
                 AND NULLIF(btrim(runtime.config_path), '') IS NULL
                THEN 'otel_config_path_missing'
            END,
            CASE
                WHEN runtime.deployment_mode = 'container'
                 AND NULLIF(btrim(runtime.container_name), '') IS NULL
                THEN 'otel_container_name_missing'
            END,
            CASE WHEN server.internal_ip IS NULL THEN 'prometheus_monitor_host_missing' END,
            CASE WHEN runtime.endpoint_port IS NULL THEN 'prometheus_monitor_port_missing' END
        ]::text[], NULL) AS readiness_reasons,
        GREATEST(
            runtime.updated_at,
            family.updated_at,
            server.updated_at,
            environment.updated_at,
            COALESCE(gateway.source_changed_at, '-infinity'::timestamptz),
            COALESCE(deployment.updated_at, '-infinity'::timestamptz),
            COALESCE(agent.updated_at, '-infinity'::timestamptz),
            COALESCE(department.updated_at, '-infinity'::timestamptz),
            COALESCE(snapshot.discovered_at, '-infinity'::timestamptz)
        ) AS source_updated_at,
        jsonb_build_object(
            'runtime', runtime.row_version,
            'runtime_family', family.row_version,
            'server', server.row_version,
            'environment', environment.row_version,
            'gateway_identifier', gateway.row_version,
            'agent_deployment', deployment.row_version,
            'primary_agent', agent.row_version,
            'department', department.row_version,
            'discovery_snapshot', snapshot.snapshot_id
        ) AS source_revision
    FROM asset.runtime_instance runtime
    JOIN asset.runtime_family family
      ON family.runtime_family_id = runtime.runtime_family_id
    JOIN asset.server_asset server
      ON server.server_id = runtime.server_id
    JOIN asset.environment environment
      ON environment.environment_id = server.environment_id
    LEFT JOIN asset.agent_deployment deployment
      ON deployment.runtime_id = runtime.runtime_id
     AND deployment.lifecycle_status = 'active'
    LEFT JOIN asset.agent_asset agent
      ON agent.agent_id = deployment.primary_agent_id
    LEFT JOIN org.department department
      ON department.department_id = agent.owning_department_id
    LEFT JOIN LATERAL (
        SELECT
            identifier.source_system,
            identifier.identifier_type,
            identifier.identifier_value,
            identifier.row_version,
            GREATEST(identifier.valid_from, identifier.created_at) AS source_changed_at
        FROM asset.external_identifier identifier
        WHERE identifier.runtime_id = runtime.runtime_id
          AND identifier.valid_to IS NULL
          AND identifier.identifier_type IN ('gateway_id', 'legacy_runtime_id')
        ORDER BY
            CASE identifier.identifier_type WHEN 'gateway_id' THEN 0 ELSE 1 END,
            identifier.valid_from DESC,
            identifier.created_at DESC,
            identifier.external_identifier_id
        LIMIT 1
    ) gateway ON true
    LEFT JOIN LATERAL (
        SELECT
            discovery.snapshot_id,
            discovery.discovery_status,
            discovery.actual_version,
            discovery.content_hash,
            discovery.discovered_at
        FROM asset.runtime_discovery_snapshot discovery
        WHERE discovery.runtime_id = runtime.runtime_id
        ORDER BY discovery.discovered_at DESC, discovery.snapshot_id DESC
        LIMIT 1
    ) snapshot ON true
    WHERE runtime.runtime_role = 'gateway'
), gateway_readiness AS (
    SELECT
        gateway_projection.*,
        (
            identity_ready
            AND lifecycle_ready
            AND otel_configuration_ready
        ) AS otel_ready,
        (
            identity_ready
            AND lifecycle_ready
            AND prometheus_target_ready
        ) AS prometheus_ready
    FROM gateway_projection
), gateway_contract AS (
    SELECT
        gateway_readiness.*,
        (otel_ready OR prometheus_ready) AS monitoring_eligible
    FROM gateway_readiness
)
SELECT
    gateway_contract.*,
    encode(
        digest(
            (
                to_jsonb(gateway_contract)
                - ARRAY['source_updated_at', 'source_revision']::text[]
            )::text,
            'sha256'
        ),
        'hex'
    ) AS contract_content_hash
FROM gateway_contract;

COMMENT ON VIEW integration.observability_gateway_v2 IS
    'Versioned one-row-per-Gateway Runtime contract for independently deployed observability; contains no SSH, Gateway token, password or application secret material.';
COMMENT ON COLUMN integration.observability_gateway_v2.runtime_id IS
    'Canonical immutable management-platform identity and primary telemetry join key.';
COMMENT ON COLUMN integration.observability_gateway_v2.gateway_id IS
    'Current external Gateway identity; gateway_id is preferred over legacy_runtime_id.';
COMMENT ON COLUMN integration.observability_gateway_v2.display_name IS
    'Operator-facing label in lobster_name-server_display_id form; never an identity key.';
COMMENT ON COLUMN integration.observability_gateway_v2.identity_ready IS
    'True when Gateway external identity and the current active primary-Agent deployment are present.';
COMMENT ON COLUMN integration.observability_gateway_v2.lifecycle_ready IS
    'True when Environment, Server, Runtime and current primary Agent are monitorable and active.';
COMMENT ON COLUMN integration.observability_gateway_v2.otel_ready IS
    'Management-side non-secret prerequisites for OTel configuration are complete; this does not assert live telemetry delivery.';
COMMENT ON COLUMN integration.observability_gateway_v2.prometheus_ready IS
    'Management-side identity, lifecycle and scrape-target prerequisites are complete; Gateway token presence remains an observability-module responsibility.';
COMMENT ON COLUMN integration.observability_gateway_v2.monitoring_eligible IS
    'True when at least one of the OTel or Prometheus management-side paths is ready.';
COMMENT ON COLUMN integration.observability_gateway_v2.readiness_reasons IS
    'Stable machine-readable codes for every unmet identity, lifecycle or channel prerequisite; may be non-empty when only one channel is ready.';
COMMENT ON COLUMN integration.observability_gateway_v2.source_updated_at IS
    'Best source timestamp across projected management records; use contract_content_hash for semantic change detection.';
COMMENT ON COLUMN integration.observability_gateway_v2.source_revision IS
    'Non-secret source row versions and latest discovery snapshot identity for diagnostics.';
COMMENT ON COLUMN integration.observability_gateway_v2.contract_content_hash IS
    'SHA-256 of the semantic view row excluding source timestamps and row-version diagnostics.';

COMMIT;
