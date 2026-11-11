\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM asset.agent_deployment
        WHERE lifecycle_status = 'active'
        GROUP BY runtime_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'cannot enforce one active primary agent per Runtime: duplicate active Runtime deployments exist'
            USING ERRCODE = '23505';
    END IF;
END;
$$;

CREATE UNIQUE INDEX uq_runtime_one_active_primary_agent
    ON asset.agent_deployment (runtime_id)
    WHERE lifecycle_status = 'active';

CREATE SCHEMA integration;

CREATE VIEW integration.observability_gateway_v1 AS
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
    runtime.lifecycle_status::text AS lifecycle_status,
    environment.environment_code,
    environment.stage AS environment_stage,
    environment.region,
    environment.operations_team,
    runtime.desired_version,
    snapshot.actual_version,
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
    GREATEST(
        runtime.updated_at,
        server.updated_at,
        environment.updated_at,
        COALESCE(deployment.updated_at, '-infinity'::timestamptz),
        COALESCE(agent.updated_at, '-infinity'::timestamptz),
        COALESCE(department.updated_at, '-infinity'::timestamptz),
        COALESCE(snapshot.discovered_at, '-infinity'::timestamptz)
    ) AS source_updated_at
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
    SELECT identifier.source_system, identifier.identifier_type, identifier.identifier_value
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
    SELECT discovery.actual_version, discovery.discovered_at
    FROM asset.runtime_discovery_snapshot discovery
    WHERE discovery.runtime_id = runtime.runtime_id
    ORDER BY discovery.discovered_at DESC, discovery.snapshot_id DESC
    LIMIT 1
) snapshot ON true
WHERE runtime.runtime_role = 'gateway';

COMMENT ON VIEW integration.observability_gateway_v1 IS
    'Stable one-row-per-Gateway contract for the independently deployed observability module.';
COMMENT ON COLUMN integration.observability_gateway_v1.display_name IS
    'Operator-facing label in lobster_name-server_display_id form.';

COMMIT;
