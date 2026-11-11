\set ON_ERROR_STOP on

BEGIN;

CREATE FUNCTION pg_temp.expect_sqlstate(test_name text, expected_state text, statement text)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    actual_state text;
BEGIN
    BEGIN
        EXECUTE statement;
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS actual_state = RETURNED_SQLSTATE;
        IF actual_state <> expected_state THEN
            RAISE EXCEPTION '% expected SQLSTATE %, received %', test_name, expected_state, actual_state;
        END IF;
        RETURN;
    END;
    RAISE EXCEPTION '% expected SQLSTATE %, but statement succeeded', test_name, expected_state;
END;
$$;

INSERT INTO asset.environment (
    environment_id, environment_code, name, stage, region, operations_team, lifecycle_status
) VALUES (
    '00000000-0000-4000-8000-000000000101', 'prod', '生产环境', 'prod', 'cn-beijing', '平台运维团队', 'active'
);

INSERT INTO org.department (department_id, department_code, name) VALUES
    ('00000000-0000-4000-8000-000000000201', 'DEP-PLATFORM', '平台运维部'),
    ('00000000-0000-4000-8000-000000000202', 'DEP-FINANCE', '示例财务部'),
    ('00000000-0000-4000-8000-000000000203', 'DEP-LEGAL', '示例法务部');

INSERT INTO org.person (
    person_id, employee_no, display_name, primary_department_id
) VALUES
    ('00000000-0000-4000-8000-000000000301', 'E001', '业务管理员', '00000000-0000-4000-8000-000000000202'),
    ('00000000-0000-4000-8000-000000000302', 'E002', 'IT管理员', '00000000-0000-4000-8000-000000000201'),
    ('00000000-0000-4000-8000-000000000303', 'E003', '服务用户一', '00000000-0000-4000-8000-000000000202'),
    ('00000000-0000-4000-8000-000000000304', 'E004', '服务用户二', '00000000-0000-4000-8000-000000000202'),
    ('00000000-0000-4000-8000-000000000305', 'E005', '服务用户三', '00000000-0000-4000-8000-000000000203'),
    ('00000000-0000-4000-8000-000000000306', 'E006', '服务用户四', '00000000-0000-4000-8000-000000000203');

SAVEPOINT api_security_contract;

INSERT INTO security.user_account (
    user_id, username, display_name, password_hash, role, created_by, updated_by
) VALUES (
    '00000000-0000-4000-8000-000000000321',
    'schema-api-user', 'Schema API 用户', 'scrypt$schema-fixture',
    'operator', 'schema-test', 'schema-test'
);

INSERT INTO security.api_key (
    api_key_id, user_id, key_prefix, key_hash
) VALUES (
    '00000000-0000-4000-8000-000000000331',
    '00000000-0000-4000-8000-000000000321',
    'awk_0123456789abcdef', repeat('a', 64)
);

SELECT pg_temp.expect_sqlstate(
    'one active API key per user', '23505',
    $sql$
        INSERT INTO security.api_key (user_id, key_prefix, key_hash)
        VALUES (
            '00000000-0000-4000-8000-000000000321',
            'awk_fedcba9876543210', repeat('b', 64)
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'API key prefix follows public format', '23514',
    $sql$
        INSERT INTO security.api_key (user_id, key_prefix, key_hash, revoked_at)
        VALUES (
            '00000000-0000-4000-8000-000000000321',
            'invalid-prefix', repeat('c', 64), clock_timestamp()
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'API key hash is lowercase SHA-256 hex', '23514',
    $sql$
        INSERT INTO security.api_key (user_id, key_prefix, key_hash, revoked_at)
        VALUES (
            '00000000-0000-4000-8000-000000000321',
            'awk_1111111111111111', 'not-a-sha256-hash', clock_timestamp()
        )
    $sql$
);

UPDATE security.api_key
SET revoked_at = clock_timestamp()
WHERE api_key_id = '00000000-0000-4000-8000-000000000331';
INSERT INTO security.api_key (user_id, key_prefix, key_hash)
VALUES (
    '00000000-0000-4000-8000-000000000321',
    'awk_fedcba9876543210', repeat('b', 64)
);

INSERT INTO security.operation_confirmation (
    confirmation_id, user_id, request_method, request_path,
    query_hash, body_hash, expires_at
) VALUES (
    '00000000-0000-4000-8000-000000000341',
    '00000000-0000-4000-8000-000000000321',
    'DELETE', '/api/v1/servers/00000000-0000-4000-8000-000000000501',
    repeat('d', 64), repeat('e', 64), now() + interval '5 minutes'
);

SELECT pg_temp.expect_sqlstate(
    'confirmation method is normalized uppercase', '23514',
    $sql$
        INSERT INTO security.operation_confirmation (
            user_id, request_method, request_path, query_hash, body_hash, expires_at
        ) VALUES (
            '00000000-0000-4000-8000-000000000321',
            'delete', '/api/v1/servers/example',
            repeat('1', 64), repeat('2', 64), now() + interval '5 minutes'
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'confirmation path excludes query text', '23514',
    $sql$
        INSERT INTO security.operation_confirmation (
            user_id, request_method, request_path, query_hash, body_hash, expires_at
        ) VALUES (
            '00000000-0000-4000-8000-000000000321',
            'DELETE', '/api/v1/servers/example?row_version=1',
            repeat('1', 64), repeat('2', 64), now() + interval '5 minutes'
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'confirmation fingerprint stores SHA-256 hashes', '23514',
    $sql$
        INSERT INTO security.operation_confirmation (
            user_id, request_method, request_path, query_hash, body_hash, expires_at
        ) VALUES (
            '00000000-0000-4000-8000-000000000321',
            'POST', '/api/v1/automation/runs',
            'invalid', repeat('2', 64), now() + interval '5 minutes'
        )
    $sql$
);

DO $$
BEGIN
    IF to_regclass('security.api_key') IS NULL
       OR to_regclass('security.operation_confirmation') IS NULL THEN
        RAISE EXCEPTION 'API security tables were not created';
    END IF;
    IF to_regclass('security.uq_api_key_one_active_per_user') IS NULL THEN
        RAISE EXCEPTION 'active API key uniqueness index was not created';
    END IF;
    IF to_regclass('security.ix_operation_confirmation_active_user') IS NULL THEN
        RAISE EXCEPTION 'active confirmation lookup index was not created';
    END IF;
END;
$$;

ROLLBACK TO SAVEPOINT api_security_contract;
RELEASE SAVEPOINT api_security_contract;

SAVEPOINT organization_master_guard_contract;

SELECT pg_temp.expect_sqlstate(
    'department code is immutable', 'P0001',
    $sql$
        UPDATE org.department SET department_code = 'DEP-CHANGED'
        WHERE department_id = '00000000-0000-4000-8000-000000000201'
    $sql$
);
SELECT pg_temp.expect_sqlstate(
    'employee number is immutable', 'P0001',
    $sql$
        UPDATE org.person SET employee_no = 'E-CHANGED'
        WHERE person_id = '00000000-0000-4000-8000-000000000301'
    $sql$
);
SELECT pg_temp.expect_sqlstate(
    'department cannot be physically deleted', 'P0001',
    $sql$
        DELETE FROM org.department
        WHERE department_id = '00000000-0000-4000-8000-000000000201'
    $sql$
);
SELECT pg_temp.expect_sqlstate(
    'person cannot be physically deleted', 'P0001',
    $sql$
        DELETE FROM org.person
        WHERE person_id = '00000000-0000-4000-8000-000000000301'
    $sql$
);

UPDATE org.department SET name = '允许改名验证'
WHERE department_id = '00000000-0000-4000-8000-000000000201';
UPDATE org.person SET display_name = '允许改名验证'
WHERE person_id = '00000000-0000-4000-8000-000000000301';

DO $$
DECLARE
    department_version bigint;
    person_version bigint;
BEGIN
    SELECT row_version INTO department_version FROM org.department
    WHERE department_id = '00000000-0000-4000-8000-000000000201';
    SELECT row_version INTO person_version FROM org.person
    WHERE person_id = '00000000-0000-4000-8000-000000000301';
    IF department_version <> 2 OR person_version <> 2 THEN
        RAISE EXCEPTION 'organization mutable fields did not advance row versions';
    END IF;
END;
$$;

ROLLBACK TO SAVEPOINT organization_master_guard_contract;
RELEASE SAVEPOINT organization_master_guard_contract;

INSERT INTO asset.cloud_provider (
    cloud_provider_id, provider_code, name, provider_kind
) VALUES (
    '00000000-0000-4000-8000-000000000401', 'huawei_cloud', '华为云', 'public_cloud'
);

INSERT INTO asset.server_asset (
    server_id, environment_id, cloud_provider_id, provider_instance_id, name,
    lifecycle_status, cpu_cores, memory_gib, disk_gib, internal_ip, public_ip,
    os_name, os_version, architecture, work_account
) VALUES
    (
        '00000000-0000-4000-8000-000000000501',
        '00000000-0000-4000-8000-000000000101',
        '00000000-0000-4000-8000-000000000401',
        'ECS-001', '裸金属宿主机', 'active', 16, 64, 500,
        '10.0.0.11', '203.0.113.11', 'Ubuntu', '24.04', 'amd64', 'openclaw'
    ),
    (
        '00000000-0000-4000-8000-000000000502',
        '00000000-0000-4000-8000-000000000101',
        '00000000-0000-4000-8000-000000000401',
        'ECS-002', '容器宿主机', 'active', 32, 128, 1000,
        '10.0.0.12', NULL, 'Ubuntu', '24.04', 'amd64', 'runtime'
    ),
    (
        '00000000-0000-4000-8000-000000000503',
        '00000000-0000-4000-8000-000000000101',
        '00000000-0000-4000-8000-000000000401',
        'ECS-003', '规划服务器', 'planned', 8, 32, 200,
        '10.0.0.13', NULL, 'EulerOS', '2.0', 'amd64', 'runtime'
    );

INSERT INTO asset.runtime_family (runtime_family_id, family_code, name) VALUES
    ('00000000-0000-4000-8000-000000000601', 'openclaw', 'OpenClaw'),
    ('00000000-0000-4000-8000-000000000602', 'hermes', 'Hermes Agent');

INSERT INTO asset.runtime_instance (
    runtime_id, server_id, runtime_family_id, name, deployment_mode,
    lifecycle_status, observed_status, endpoint_port, runtime_account, config_path,
    container_name, image_ref
) VALUES
    (
        '00000000-0000-4000-8000-000000000701',
        '00000000-0000-4000-8000-000000000501',
        '00000000-0000-4000-8000-000000000601',
        'OpenClaw 主运行时', 'baremetal', 'active', 'running', 16402, 'openclaw',
        '/home/openclaw/.openclaw/openclaw.json', NULL, NULL
    ),
    (
        '00000000-0000-4000-8000-000000000702',
        '00000000-0000-4000-8000-000000000502',
        '00000000-0000-4000-8000-000000000602',
        'Hermes 容器 Runtime', 'container', 'active', 'running', 18080, 'runtime',
        NULL, 'hermes-main', 'registry.example/hermes:1.0'
    );

INSERT INTO asset.management_group (
    group_id, group_code, name, resource_type, purpose
) VALUES
    ('00000000-0000-4000-8000-000000000801', 'server-prod', '生产服务器组', 'server', '生产资源'),
    ('00000000-0000-4000-8000-000000000802', 'server-container', '容器服务器组', 'server', '容器宿主机'),
    ('00000000-0000-4000-8000-000000000803', 'agent-priority', '重点智能体组', 'agent', '重点保障'),
    ('00000000-0000-4000-8000-000000000804', 'agent-finance', '财务智能体组', 'agent', '财务领域');

INSERT INTO asset.server_group_member (group_id, server_id) VALUES
    ('00000000-0000-4000-8000-000000000801', '00000000-0000-4000-8000-000000000502'),
    ('00000000-0000-4000-8000-000000000802', '00000000-0000-4000-8000-000000000502');

INSERT INTO asset.agent_asset (
    agent_id, name, structure_role, parent_agent_id, service_archetype,
    lifecycle_status, owning_department_id, business_admin_person_id,
    it_admin_person_id, primary_service_person_id,
    primary_service_department_id, business_domain, business_scenario
) VALUES
    (
        '00000000-0000-4000-8000-000000000901', '员工个人数字助手', 'primary', NULL,
        'personal', 'active', '00000000-0000-4000-8000-000000000201',
        '00000000-0000-4000-8000-000000000301', '00000000-0000-4000-8000-000000000302',
        '00000000-0000-4000-8000-000000000303', NULL, NULL, NULL
    ),
    (
        '00000000-0000-4000-8000-000000000911', '个人信息检索子智能体', 'subagent',
        '00000000-0000-4000-8000-000000000901', 'personal', 'active', NULL,
        NULL, NULL, NULL, NULL, NULL, NULL
    ),
    (
        '00000000-0000-4000-8000-000000000902', '财务部综合助手', 'primary', NULL,
        'department', 'active', '00000000-0000-4000-8000-000000000202',
        '00000000-0000-4000-8000-000000000301', '00000000-0000-4000-8000-000000000302',
        NULL, '00000000-0000-4000-8000-000000000202', NULL, NULL
    ),
    (
        '00000000-0000-4000-8000-000000000912', '财务报表子智能体', 'subagent',
        '00000000-0000-4000-8000-000000000902', 'department', 'active', NULL,
        NULL, NULL, NULL, NULL, NULL, NULL
    ),
    (
        '00000000-0000-4000-8000-000000000903', '税务合规智能体', 'primary', NULL,
        'business', 'active', '00000000-0000-4000-8000-000000000203',
        '00000000-0000-4000-8000-000000000301', '00000000-0000-4000-8000-000000000302',
        NULL, NULL, '财税合规', '税务政策分析'
    ),
    (
        '00000000-0000-4000-8000-000000000913', '税务政策子智能体', 'subagent',
        '00000000-0000-4000-8000-000000000903', 'business', 'active', NULL,
        NULL, NULL, NULL, NULL, NULL, NULL
    ),
    (
        '00000000-0000-4000-8000-000000000904', '合同风险智能体', 'primary', NULL,
        'business', 'active', '00000000-0000-4000-8000-000000000203',
        '00000000-0000-4000-8000-000000000301', '00000000-0000-4000-8000-000000000302',
        NULL, NULL, '合同管理', '合同风险审查'
    );

INSERT INTO asset.agent_accessible_person (agent_id, person_id) VALUES
    ('00000000-0000-4000-8000-000000000901', '00000000-0000-4000-8000-000000000303'),
    ('00000000-0000-4000-8000-000000000901', '00000000-0000-4000-8000-000000000304'),
    ('00000000-0000-4000-8000-000000000901', '00000000-0000-4000-8000-000000000305');

INSERT INTO asset.agent_group_member (group_id, agent_id) VALUES
    ('00000000-0000-4000-8000-000000000803', '00000000-0000-4000-8000-000000000902'),
    ('00000000-0000-4000-8000-000000000804', '00000000-0000-4000-8000-000000000902');

INSERT INTO asset.agent_deployment (
    agent_deployment_id, primary_agent_id, runtime_id, lifecycle_status
) VALUES
    ('00000000-0000-4000-8000-000000001001', '00000000-0000-4000-8000-000000000901', '00000000-0000-4000-8000-000000000701', 'active'),
    ('00000000-0000-4000-8000-000000001004', '00000000-0000-4000-8000-000000000904', '00000000-0000-4000-8000-000000000702', 'active');

INSERT INTO asset.agent_deployment_member (
    deployment_member_id, agent_deployment_id, runtime_id, agent_id,
    member_role, parent_member_id, runtime_agent_key, mapping_status
) VALUES
    ('00000000-0000-4000-8000-000000001101', '00000000-0000-4000-8000-000000001001', '00000000-0000-4000-8000-000000000701', '00000000-0000-4000-8000-000000000901', 'primary', NULL, 'personal-main', 'verified'),
    ('00000000-0000-4000-8000-000000001111', '00000000-0000-4000-8000-000000001001', '00000000-0000-4000-8000-000000000701', '00000000-0000-4000-8000-000000000911', 'subagent', '00000000-0000-4000-8000-000000001101', 'personal-research', 'verified'),
    ('00000000-0000-4000-8000-000000001104', '00000000-0000-4000-8000-000000001004', '00000000-0000-4000-8000-000000000702', '00000000-0000-4000-8000-000000000904', 'primary', NULL, 'legal-main', 'verified');

DO $$
DECLARE
    deployment_count integer;
    access_count integer;
BEGIN
    SELECT count(*) INTO deployment_count
    FROM asset.agent_deployment
    WHERE runtime_id = '00000000-0000-4000-8000-000000000701'
      AND lifecycle_status = 'active';
    IF deployment_count <> 1 THEN
        RAISE EXCEPTION 'Runtime expected exactly one active primary agent, received %', deployment_count;
    END IF;

    SELECT count(*) INTO access_count
    FROM asset.agent_accessible_person
    WHERE agent_id = '00000000-0000-4000-8000-000000000901'
      AND valid_to IS NULL;
    IF access_count <> 3 THEN
        RAISE EXCEPTION 'personal agent expected 3 accessible people, received %', access_count;
    END IF;
END;
$$;

SELECT pg_temp.expect_sqlstate(
    'duplicate current provider instance', '23505',
    $sql$
        INSERT INTO asset.server_asset (
            environment_id, cloud_provider_id, provider_instance_id, name, lifecycle_status
        ) VALUES (
            '00000000-0000-4000-8000-000000000101',
            '00000000-0000-4000-8000-000000000401',
            'ECS-001', '重复云实例', 'active'
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'duplicate current internal IP', '23505',
    $sql$
        INSERT INTO asset.server_asset (
            environment_id, cloud_provider_id, provider_instance_id, name, lifecycle_status, internal_ip
        ) VALUES (
            '00000000-0000-4000-8000-000000000101',
            '00000000-0000-4000-8000-000000000401',
            'ECS-099', '重复 IP', 'active', '10.0.0.11'
        )
    $sql$
);

SAVEPOINT shared_public_ip_contract;

INSERT INTO asset.server_asset (
    environment_id, cloud_provider_id, provider_instance_id, name,
    lifecycle_status, internal_ip, public_ip
) VALUES (
    '00000000-0000-4000-8000-000000000101',
    '00000000-0000-4000-8000-000000000401',
    'ECS-004', '共享公网出口服务器', 'active',
    '10.0.0.14', '203.0.113.11'
);

DO $$
DECLARE
    shared_public_ip_count integer;
BEGIN
    SELECT count(*) INTO shared_public_ip_count
    FROM asset.server_asset
    WHERE environment_id = '00000000-0000-4000-8000-000000000101'
      AND public_ip = '203.0.113.11'
      AND lifecycle_status <> 'retired';
    IF shared_public_ip_count <> 2 THEN
        RAISE EXCEPTION 'shared public IP expected 2 active servers, received %', shared_public_ip_count;
    END IF;
END;
$$;

ROLLBACK TO SAVEPOINT shared_public_ip_contract;

SELECT pg_temp.expect_sqlstate(
    'container Runtime requires image and container name', '23514',
    $sql$
        INSERT INTO asset.runtime_instance (
            server_id, runtime_family_id, name, deployment_mode, lifecycle_status
        ) VALUES (
            '00000000-0000-4000-8000-000000000502',
            '00000000-0000-4000-8000-000000000601',
            '不完整容器 Runtime', 'container', 'planned'
        )
    $sql$
);

UPDATE asset.runtime_instance
SET management_driver = 'systemd_user',
    management_target = 'openclaw-gateway.service',
    command_path = '/home/openclaw/.local/bin/openclaw'
WHERE runtime_id = '00000000-0000-4000-8000-000000000701';

DO $$
DECLARE
    driver text;
    command text;
BEGIN
    SELECT management_driver::text, command_path INTO driver, command
    FROM asset.runtime_instance
    WHERE runtime_id = '00000000-0000-4000-8000-000000000701';
    IF driver <> 'systemd_user' OR command <> '/home/openclaw/.local/bin/openclaw' THEN
        RAISE EXCEPTION 'systemd user runtime configuration was not persisted';
    END IF;
END;
$$;

SELECT pg_temp.expect_sqlstate(
    'runtime command path must be absolute', '23514',
    $sql$
        UPDATE asset.runtime_instance
        SET command_path = '.local/bin/openclaw'
        WHERE runtime_id = '00000000-0000-4000-8000-000000000701'
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'systemd user management requires baremetal deployment', '23514',
    $sql$
        UPDATE asset.runtime_instance
        SET management_driver = 'systemd_user',
            management_target = 'hermes.service'
        WHERE runtime_id = '00000000-0000-4000-8000-000000000702'
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'personal agent requires primary service person', '23514',
    $sql$
        INSERT INTO asset.agent_asset (
            name, structure_role, service_archetype, lifecycle_status
        ) VALUES ('不完整个人智能体', 'primary', 'personal', 'requested')
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'active agent requires administrators', '23514',
    $sql$
        INSERT INTO asset.agent_asset (
            name, structure_role, service_archetype, lifecycle_status,
            business_domain, business_scenario
        ) VALUES ('缺少管理员智能体', 'primary', 'business', 'active', '测试', '缺少管理员')
    $sql$
);

INSERT INTO asset.agent_channel_configuration (
    agent_id, channel_type, application_name, app_id, encrypted_app_secret, enabled
) VALUES (
    '00000000-0000-4000-8000-000000000911',
    'feishu', '子智能体独立应用', 'cli_schema_subagent', 'encrypted-test-value', false
);

SELECT pg_temp.expect_sqlstate(
    'one active deployment per primary agent', '23505',
    $sql$
        INSERT INTO asset.agent_deployment (
            primary_agent_id, runtime_id, lifecycle_status
        ) VALUES (
            '00000000-0000-4000-8000-000000000901',
            '00000000-0000-4000-8000-000000000702',
            'active'
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'one active primary agent per Runtime', '23505',
    $sql$
        INSERT INTO asset.agent_deployment (
            primary_agent_id, runtime_id, lifecycle_status
        ) VALUES (
            '00000000-0000-4000-8000-000000000902',
            '00000000-0000-4000-8000-000000000701',
            'active'
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'Deployment primary_agent_id must reference a Primary Agent', '23503',
    $sql$
        INSERT INTO asset.agent_deployment (
            primary_agent_id, runtime_id, lifecycle_status
        ) VALUES (
            '00000000-0000-4000-8000-000000000911',
            '00000000-0000-4000-8000-000000000702',
            'planned'
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'referenced Primary Agent cannot become a Subagent', '23503',
    $sql$
        UPDATE asset.agent_asset
        SET structure_role = 'subagent',
            parent_agent_id = '00000000-0000-4000-8000-000000000903',
            business_admin_person_id = NULL,
            it_admin_person_id = NULL,
            business_domain = NULL,
            business_scenario = NULL
        WHERE agent_id = '00000000-0000-4000-8000-000000000904'
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'Deployment primary role discriminator is immutable', '23514',
    $sql$
        UPDATE asset.agent_deployment
        SET primary_agent_structure_role = 'subagent'
        WHERE agent_deployment_id = '00000000-0000-4000-8000-000000001004'
    $sql$
);

DO $$
BEGIN
    IF to_regclass('asset.uq_agent_identity_structure_role') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM pg_constraint
           WHERE conrelid = 'asset.agent_deployment'::regclass
             AND conname = 'fk_agent_deployment_primary_role'
       )
       OR EXISTS (
           SELECT 1
           FROM asset.agent_deployment deployment
           JOIN asset.agent_asset agent
             ON agent.agent_id = deployment.primary_agent_id
           WHERE deployment.primary_agent_structure_role <> 'primary'
              OR agent.structure_role <> 'primary'
       ) THEN
        RAISE EXCEPTION 'primary-Agent Deployment integrity migration is incomplete';
    END IF;
END;
$$;

SELECT pg_temp.expect_sqlstate(
    'deployment member Runtime must match deployment', '23503',
    $sql$
        INSERT INTO asset.agent_deployment_member (
            agent_deployment_id, runtime_id, agent_id, member_role,
            parent_member_id, runtime_agent_key, mapping_status
        ) VALUES (
            '00000000-0000-4000-8000-000000001004',
            '00000000-0000-4000-8000-000000000701',
            '00000000-0000-4000-8000-000000000913',
            'subagent', '00000000-0000-4000-8000-000000001104',
            'wrong-runtime', 'verified'
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'verified deployment member requires key', '23514',
    $sql$
        INSERT INTO asset.agent_deployment_member (
            agent_deployment_id, runtime_id, agent_id, member_role,
            parent_member_id, runtime_agent_key, mapping_status
        ) VALUES (
            '00000000-0000-4000-8000-000000001001',
            '00000000-0000-4000-8000-000000000701',
            '00000000-0000-4000-8000-000000000913',
            'subagent', '00000000-0000-4000-8000-000000001101', NULL, 'verified'
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'one current primary member per deployment', '23505',
    $sql$
        INSERT INTO asset.agent_deployment_member (
            agent_deployment_id, runtime_id, agent_id, member_role,
            runtime_agent_key, mapping_status
        ) VALUES (
            '00000000-0000-4000-8000-000000001001',
            '00000000-0000-4000-8000-000000000701',
            '00000000-0000-4000-8000-000000000904',
            'primary', 'second-primary', 'verified'
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'Runtime agent key unique among current members', '23505',
    $sql$
        INSERT INTO asset.agent_deployment_member (
            agent_deployment_id, runtime_id, agent_id, member_role,
            parent_member_id, runtime_agent_key, mapping_status
        ) VALUES (
            '00000000-0000-4000-8000-000000001001',
            '00000000-0000-4000-8000-000000000701',
            '00000000-0000-4000-8000-000000000912',
            'subagent', '00000000-0000-4000-8000-000000001101',
            'personal-main', 'verified'
        )
    $sql$
);

INSERT INTO asset.external_identifier (
    source_system, identifier_type, identifier_value, server_id, runtime_id
) VALUES
    ('legacy_inventory', 'server_id', 'ECS-001', '00000000-0000-4000-8000-000000000501', NULL),
    ('openclaw', 'gateway_id', 'gateway-701', NULL, '00000000-0000-4000-8000-000000000701');

SELECT pg_temp.expect_sqlstate(
    'external identifier targets exactly one object', '23514',
    $sql$
        INSERT INTO asset.external_identifier (
            source_system, identifier_type, identifier_value, server_id, runtime_id
        ) VALUES (
            'legacy_inventory', 'invalid', 'two-targets',
            '00000000-0000-4000-8000-000000000501',
            '00000000-0000-4000-8000-000000000701'
        )
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'current external identifier namespace unique', '23505',
    $sql$
        INSERT INTO asset.external_identifier (
            source_system, identifier_type, identifier_value, server_id
        ) VALUES (
            'legacy_inventory', 'server_id', 'ECS-001',
            '00000000-0000-4000-8000-000000000502'
        )
    $sql$
);

UPDATE asset.server_asset
SET name = '裸金属宿主机（改名）'
WHERE server_id = '00000000-0000-4000-8000-000000000501';

DO $$
DECLARE
    version bigint;
BEGIN
    SELECT row_version INTO version
    FROM asset.server_asset
    WHERE server_id = '00000000-0000-4000-8000-000000000501';
    IF version <> 2 THEN
        RAISE EXCEPTION 'row_version trigger expected 2, received %', version;
    END IF;
END;
$$;

INSERT INTO asset.runtime_discovery_snapshot (
    snapshot_id, runtime_id, discovered_at, discovery_status, discovery_source,
    actual_version, observed_agent_count, content_hash, raw_payload
) VALUES
    (
        '00000000-0000-4000-8000-000000001201',
        '00000000-0000-4000-8000-000000000701', now() - interval '2 minutes', 'succeeded', 'test',
        '2026.6.11', 6, repeat('a', 64), '{"version":"2026.6.11"}'
    ),
    (
        '00000000-0000-4000-8000-000000001202',
        '00000000-0000-4000-8000-000000000701', now() - interval '1 minute', 'succeeded', 'test',
        '2026.6.11', 6, repeat('a', 64), '{"version":"2026.6.11"}'
    );

DO $$
DECLARE
    gateway_row integration.observability_gateway_v1%ROWTYPE;
BEGIN
    SELECT * INTO STRICT gateway_row
    FROM integration.observability_gateway_v1
    WHERE runtime_id = '00000000-0000-4000-8000-000000000701';
    IF gateway_row.gateway_id <> 'gateway-701'
       OR gateway_row.primary_agent_id <> '00000000-0000-4000-8000-000000000901'
       OR gateway_row.lobster_name <> '员工个人数字助手'
       OR gateway_row.server_display_id <> 'ECS-001'
       OR gateway_row.display_name <> '员工个人数字助手-ECS-001'
       OR gateway_row.actual_version <> '2026.6.11'
       OR gateway_row.monitoring_ready IS NOT TRUE THEN
        RAISE EXCEPTION 'observability Gateway integration view returned an invalid contract row';
    END IF;
END;
$$;

DO $$
DECLARE
    gateway_row integration.observability_gateway_v2%ROWTYPE;
    gateway_count integer;
    distinct_runtime_count integer;
BEGIN
    SELECT count(*), count(DISTINCT runtime_id)
    INTO gateway_count, distinct_runtime_count
    FROM integration.observability_gateway_v2;
    IF gateway_count <> 2 OR distinct_runtime_count <> gateway_count THEN
        RAISE EXCEPTION
            'observability Gateway v2 expected one row per Gateway Runtime, received % rows and % Runtime IDs',
            gateway_count, distinct_runtime_count;
    END IF;

    SELECT * INTO STRICT gateway_row
    FROM integration.observability_gateway_v2
    WHERE runtime_id = '00000000-0000-4000-8000-000000000701';
    IF gateway_row.gateway_id <> 'gateway-701'
       OR gateway_row.server_id <> '00000000-0000-4000-8000-000000000501'
       OR gateway_row.primary_agent_id <> '00000000-0000-4000-8000-000000000901'
       OR gateway_row.agent_deployment_id <> '00000000-0000-4000-8000-000000001001'
       OR gateway_row.display_name <> '员工个人数字助手-ECS-001'
       OR gateway_row.runtime_account <> 'openclaw'
       OR gateway_row.config_path <> '/home/openclaw/.openclaw/openclaw.json'
       OR gateway_row.container_name IS NOT NULL
       OR gateway_row.environment_lifecycle_status <> 'active'
       OR gateway_row.server_lifecycle_status <> 'active'
       OR gateway_row.runtime_lifecycle_status <> 'active'
       OR gateway_row.primary_agent_lifecycle_status <> 'active'
       OR gateway_row.identity_ready IS NOT TRUE
       OR gateway_row.lifecycle_ready IS NOT TRUE
       OR gateway_row.otel_ready IS NOT TRUE
       OR gateway_row.prometheus_ready IS NOT TRUE
       OR gateway_row.monitoring_eligible IS NOT TRUE
       OR cardinality(gateway_row.readiness_reasons) <> 0
       OR gateway_row.source_updated_at IS NULL
       OR gateway_row.source_revision->>'runtime' IS NULL
       OR gateway_row.contract_content_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'observability Gateway v2 returned an invalid ready contract row';
    END IF;

    SELECT * INTO STRICT gateway_row
    FROM integration.observability_gateway_v2
    WHERE runtime_id = '00000000-0000-4000-8000-000000000702';
    IF gateway_row.gateway_id IS NOT NULL
       OR gateway_row.container_name <> 'hermes-main'
       OR gateway_row.identity_ready IS NOT FALSE
       OR gateway_row.otel_ready IS NOT FALSE
       OR gateway_row.prometheus_ready IS NOT FALSE
       OR gateway_row.monitoring_eligible IS NOT FALSE
       OR NOT ('gateway_id_missing' = ANY(gateway_row.readiness_reasons)) THEN
        RAISE EXCEPTION 'observability Gateway v2 did not explain its incomplete identity';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'integration'
          AND table_name = 'observability_gateway_v2'
          AND column_name ~ '(password|secret|token|credential)'
    ) THEN
        RAISE EXCEPTION 'observability Gateway v2 exposes a secret-bearing column';
    END IF;
END;
$$;

SAVEPOINT observability_gateway_v2_readiness_contract;

DO $$
DECLARE
    before_hash text;
    gateway_row integration.observability_gateway_v2%ROWTYPE;
BEGIN
    SELECT contract_content_hash INTO STRICT before_hash
    FROM integration.observability_gateway_v2
    WHERE runtime_id = '00000000-0000-4000-8000-000000000701';

    UPDATE asset.runtime_instance
    SET endpoint_port = NULL, config_path = NULL
    WHERE runtime_id = '00000000-0000-4000-8000-000000000701';

    SELECT * INTO STRICT gateway_row
    FROM integration.observability_gateway_v2
    WHERE runtime_id = '00000000-0000-4000-8000-000000000701';
    IF gateway_row.identity_ready IS NOT TRUE
       OR gateway_row.lifecycle_ready IS NOT TRUE
       OR gateway_row.otel_ready IS NOT FALSE
       OR gateway_row.prometheus_ready IS NOT FALSE
       OR gateway_row.monitoring_eligible IS NOT FALSE
       OR NOT ('otel_config_path_missing' = ANY(gateway_row.readiness_reasons))
       OR NOT ('prometheus_monitor_port_missing' = ANY(gateway_row.readiness_reasons))
       OR gateway_row.contract_content_hash = before_hash THEN
        RAISE EXCEPTION 'observability Gateway v2 readiness or content hash did not track target changes';
    END IF;
END;
$$;

ROLLBACK TO SAVEPOINT observability_gateway_v2_readiness_contract;
RELEASE SAVEPOINT observability_gateway_v2_readiness_contract;

SELECT pg_temp.expect_sqlstate(
    'snapshot is immutable', 'P0001',
    $sql$
        UPDATE asset.runtime_discovery_snapshot
        SET actual_version = 'mutated'
        WHERE snapshot_id = '00000000-0000-4000-8000-000000001201'
    $sql$
);

INSERT INTO audit.change_log (
    change_id, actor_type, actor_ref, source, object_type, object_id,
    operation, changed_fields, before_value, after_value
) VALUES (
    '00000000-0000-4000-8000-000000001301', 'system', 'schema-test', 'system',
    'server_asset', '00000000-0000-4000-8000-000000000501', 'update', ARRAY['name'],
    '{"name":"裸金属宿主机"}', '{"name":"裸金属宿主机（改名）"}'
);

SELECT pg_temp.expect_sqlstate(
    'change log is immutable', 'P0001',
    $sql$
        DELETE FROM audit.change_log
        WHERE change_id = '00000000-0000-4000-8000-000000001301'
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'referenced server cannot be deleted', '23503',
    $sql$
        DELETE FROM asset.server_asset
        WHERE server_id = '00000000-0000-4000-8000-000000000501'
    $sql$
);

INSERT INTO ingest.import_batch (
    import_batch_id, file_name, file_sha256, template_version, actor_ref
) VALUES (
    '00000000-0000-4000-8000-000000001401', 'servers.xlsx', repeat('b', 64), '1.0', 'schema-test'
);
INSERT INTO ingest.import_row (
    import_row_id, import_batch_id, sheet_name, row_number, target_object_type,
    target_object_id, expected_row_version, raw_payload, normalized_payload, status
) VALUES (
    '00000000-0000-4000-8000-000000001411',
    '00000000-0000-4000-8000-000000001401', 'Servers', 2, 'server_asset',
    '00000000-0000-4000-8000-000000000501', 2,
    '{"name":""}', '{"name":null}', 'unchanged'
);
INSERT INTO ingest.import_issue (
    import_batch_id, import_row_id, severity, issue_code, field_path, message
) VALUES (
    '00000000-0000-4000-8000-000000001401',
    '00000000-0000-4000-8000-000000001411',
    'warning', 'EMPTY_MEANS_IGNORE', 'name', 'empty cell does not modify current value'
);
INSERT INTO ingest.import_change (
    import_row_id, field_path, action, before_value, after_value, selected
) VALUES (
    '00000000-0000-4000-8000-000000001411', 'name', 'ignore',
    '"裸金属宿主机（改名）"', NULL, false
);

UPDATE asset.agent_deployment_member
SET valid_to = clock_timestamp() + interval '1 second'
WHERE agent_deployment_id = '00000000-0000-4000-8000-000000001004'
  AND valid_to IS NULL;
UPDATE asset.agent_deployment
SET lifecycle_status = 'inactive', valid_to = clock_timestamp() + interval '1 second'
WHERE agent_deployment_id = '00000000-0000-4000-8000-000000001004';

INSERT INTO asset.runtime_instance (
    runtime_id, server_id, runtime_family_id, name, deployment_mode,
    lifecycle_status, observed_status, container_name, image_ref
) VALUES (
    '00000000-0000-4000-8000-000000000705',
    '00000000-0000-4000-8000-000000000502',
    '00000000-0000-4000-8000-000000000601',
    'OpenClaw 迁移目标 Runtime', 'container', 'active', 'running',
    'openclaw-migrated', 'registry.example/openclaw:2026.6'
);
INSERT INTO asset.agent_deployment (
    agent_deployment_id, primary_agent_id, runtime_id, lifecycle_status, valid_from
) VALUES (
    '00000000-0000-4000-8000-000000001005',
    '00000000-0000-4000-8000-000000000904',
    '00000000-0000-4000-8000-000000000705', 'active',
    clock_timestamp() + interval '1 second'
);
INSERT INTO asset.agent_deployment_member (
    deployment_member_id, agent_deployment_id, runtime_id, agent_id,
    member_role, runtime_agent_key, mapping_status, valid_from
) VALUES (
    '00000000-0000-4000-8000-000000001105',
    '00000000-0000-4000-8000-000000001005',
    '00000000-0000-4000-8000-000000000705',
    '00000000-0000-4000-8000-000000000904',
    'primary', 'legal-main', 'verified', clock_timestamp() + interval '1 second'
);

DO $$
DECLARE
    active_count integer;
    history_count integer;
BEGIN
    SELECT count(*) INTO active_count
    FROM asset.agent_deployment
    WHERE primary_agent_id = '00000000-0000-4000-8000-000000000904'
      AND lifecycle_status = 'active';
    SELECT count(*) INTO history_count
    FROM asset.agent_deployment
    WHERE primary_agent_id = '00000000-0000-4000-8000-000000000904';
    IF active_count <> 1 OR history_count <> 2 THEN
        RAISE EXCEPTION 'migration expected one active and two historical deployments, received % and %',
            active_count, history_count;
    END IF;
END;
$$;

SAVEPOINT operation_event_contract;

INSERT INTO automation.operation_run (
    run_id, operation_code, requested_by, target_count, playbook_version
) VALUES (
    '00000000-0000-4000-8000-000000001990',
    'server.schema_contract', 'schema-test', 1, '1.0.0+schema'
);

INSERT INTO automation.operation_host_result (
    host_result_id, run_id, server_id, target_key, target_plan
) VALUES (
    '00000000-0000-4000-8000-000000001991',
    '00000000-0000-4000-8000-000000001990',
    '00000000-0000-4000-8000-000000000501',
    '00000000-0000-4000-8000-000000000501',
    '{"operation":"schema contract"}'
);

SELECT pg_temp.expect_sqlstate(
    'operation target plan must be an object', '23514',
    $sql$
        UPDATE automation.operation_host_result
        SET target_plan = '[]'
        WHERE host_result_id = '00000000-0000-4000-8000-000000001991'
    $sql$
);

INSERT INTO automation.operation_event (
    operation_event_id, run_id, sequence_no, phase, event_type, stdout
) VALUES (
    '00000000-0000-4000-8000-000000001992',
    '00000000-0000-4000-8000-000000001990',
    1, 'dispatch', 'task_queued', 'queued'
);

SELECT pg_temp.expect_sqlstate(
    'operation event sequence is unique per run', '23505',
    $sql$
        INSERT INTO automation.operation_event (run_id, sequence_no, phase, event_type)
        VALUES ('00000000-0000-4000-8000-000000001990', 1, 'dispatch', 'duplicate')
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'operation events are immutable on update', '23514',
    $sql$
        UPDATE automation.operation_event SET stdout = 'changed'
        WHERE operation_event_id = '00000000-0000-4000-8000-000000001992'
    $sql$
);

SELECT pg_temp.expect_sqlstate(
    'operation events are immutable on delete', '23514',
    $sql$
        DELETE FROM automation.operation_event
        WHERE operation_event_id = '00000000-0000-4000-8000-000000001992'
    $sql$
);

ROLLBACK TO SAVEPOINT operation_event_contract;

COMMIT;

SELECT 'core asset PostgreSQL schema tests passed' AS result;
