from uuid import UUID


def add_filter(clauses, params, value, sql, name):
    if value is not None and value != "":
        clauses.append(sql)
        params[name] = value


def paginated(connection, select_sql, count_sql, clauses, params, limit, offset):
    where = " AND ".join(clauses) if clauses else "TRUE"
    query_params = dict(params, limit=limit, offset=offset)
    rows = list(
        connection.execute(
            f"{select_sql} WHERE {where} ORDER BY name, stable_id LIMIT %(limit)s OFFSET %(offset)s",
            query_params,
        ).fetchall()
    )
    total = connection.execute(f"{count_sql} WHERE {where}", params).fetchone()["total"]
    return {
        "data": rows,
        "page": {"limit": limit, "offset": offset, "total": total},
    }


class CoreAssetQueries:
    def __init__(self, database):
        self.database = database

    def health(self):
        return self.database.fetch_one(
            """
            SELECT current_database() AS database,
                   current_user AS database_user,
                   current_setting('transaction_read_only') AS transaction_read_only
            """
        )

    def overview(self):
        with self.database.connection() as connection:
            counts = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM asset.server_asset) AS servers,
                    (SELECT count(*) FROM asset.runtime_instance) AS runtimes,
                    (SELECT count(*) FROM asset.agent_asset WHERE structure_role = 'primary') AS primary_agents,
                    (SELECT count(*) FROM asset.agent_asset WHERE structure_role = 'subagent') AS subagents,
                    (SELECT count(*) FROM asset.agent_deployment WHERE lifecycle_status = 'active') AS active_deployments,
                    (SELECT count(*) FROM asset.management_group WHERE lifecycle_status = 'active') AS active_groups,
                    (SELECT count(*) FROM ingest.import_batch) AS import_batches,
                    (SELECT count(*) FROM ingest.import_issue WHERE severity = 'error') AS data_quality_errors,
                    (SELECT count(*) FROM ingest.import_issue WHERE severity = 'warning') AS data_quality_warnings
                """
            ).fetchone()
            server_lifecycle = list(
                connection.execute(
                    "SELECT lifecycle_status::text AS status, count(*) AS count "
                    "FROM asset.server_asset GROUP BY lifecycle_status ORDER BY lifecycle_status"
                ).fetchall()
            )
            runtime_families = list(
                connection.execute(
                    """
                    SELECT rf.family_code, count(*) AS count
                    FROM asset.runtime_instance r
                    JOIN asset.runtime_family rf ON rf.runtime_family_id = r.runtime_family_id
                    GROUP BY rf.family_code ORDER BY rf.family_code
                    """
                ).fetchall()
            )
            agent_archetypes = list(
                connection.execute(
                    """
                    SELECT service_archetype::text AS archetype, count(*) AS count
                    FROM asset.agent_asset
                    WHERE structure_role = 'primary'
                    GROUP BY service_archetype ORDER BY service_archetype
                    """
                ).fetchall()
            )
            return {
                "counts": counts,
                "server_lifecycle": server_lifecycle,
                "runtime_families": runtime_families,
                "primary_agent_archetypes": agent_archetypes,
            }

    def reference_data(self):
        with self.database.connection() as connection:
            return {
                "environments": list(
                    connection.execute(
                        """
                        SELECT environment_id, environment_code, name, stage, region,
                               lifecycle_status::text AS lifecycle_status
                        FROM asset.environment ORDER BY name, environment_id
                        """
                    ).fetchall()
                ),
                "cloud_providers": list(
                    connection.execute(
                        """
                        SELECT cloud_provider_id, provider_code, name, provider_kind,
                               status::text AS status
                        FROM asset.cloud_provider ORDER BY name, cloud_provider_id
                        """
                    ).fetchall()
                ),
                "runtime_families": list(
                    connection.execute(
                        """
                        SELECT runtime_family_id, family_code, name, status::text AS status, metadata
                        FROM asset.runtime_family ORDER BY name, runtime_family_id
                        """
                    ).fetchall()
                ),
                "departments": list(
                    connection.execute(
                        """
                        SELECT d.department_id, d.department_code, d.name,
                               d.parent_department_id, parent.department_code AS parent_department_code,
                               d.is_active
                        FROM org.department d
                        LEFT JOIN org.department parent ON parent.department_id = d.parent_department_id
                        ORDER BY d.name, d.department_id
                        """
                    ).fetchall()
                ),
            }

    def people(self, limit, offset, employment_status=None, department_code=None, q=None):
        select_sql = """
            SELECT p.person_id AS stable_id, p.person_id, p.display_name AS name,
                   p.display_name, p.employee_no, p.employment_status,
                   p.primary_department_id, d.department_code, d.name AS department_name,
                   d.is_active AS department_is_active,
                   (SELECT count(*) FROM asset.agent_asset agent
                    WHERE agent.lifecycle_status <> 'retired'
                      AND (agent.business_admin_person_id = p.person_id
                           OR agent.it_admin_person_id = p.person_id
                           OR agent.primary_service_person_id = p.person_id
                           OR EXISTS (
                               SELECT 1 FROM asset.agent_accessible_person access
                               WHERE access.agent_id = agent.agent_id
                                 AND access.person_id = p.person_id
                                 AND access.valid_to IS NULL
                           ))) AS active_agent_references,
                   p.row_version, p.created_at, p.updated_at
            FROM org.person p
            LEFT JOIN org.department d ON d.department_id = p.primary_department_id
        """
        count_sql = """
            SELECT count(*) AS total
            FROM org.person p
            LEFT JOIN org.department d ON d.department_id = p.primary_department_id
        """
        clauses = []
        params = {}
        add_filter(clauses, params, employment_status, "p.employment_status = %(employment_status)s", "employment_status")
        add_filter(clauses, params, department_code, "d.department_code = %(department_code)s", "department_code")
        if q:
            clauses.append("(p.display_name ILIKE %(q)s OR p.employee_no ILIKE %(q)s)")
            params["q"] = f"%{q}%"
        with self.database.connection() as connection:
            return paginated(connection, select_sql, count_sql, clauses, params, limit, offset)

    def person(self, person_id: UUID):
        with self.database.connection() as connection:
            item = connection.execute(
                """
                SELECT p.person_id, p.employee_no, p.display_name,
                       p.primary_department_id, p.employment_status,
                       d.department_code, d.name AS department_name,
                       d.is_active AS department_is_active,
                       p.row_version, p.created_at, p.updated_at
                FROM org.person p
                LEFT JOIN org.department d ON d.department_id = p.primary_department_id
                WHERE p.person_id = %(person_id)s
                """,
                {"person_id": person_id},
            ).fetchone()
            if item is None:
                return None
            item["agent_relationships"] = list(connection.execute(
                """
                SELECT agent.agent_id, agent.name,
                       agent.structure_role::text AS structure_role,
                       agent.lifecycle_status::text AS lifecycle_status,
                       relationship.role
                FROM asset.agent_asset agent
                JOIN LATERAL (
                    SELECT 'business_admin' AS role
                    WHERE agent.business_admin_person_id = %(person_id)s
                    UNION ALL SELECT 'it_admin'
                    WHERE agent.it_admin_person_id = %(person_id)s
                    UNION ALL SELECT 'primary_service_person'
                    WHERE agent.primary_service_person_id = %(person_id)s
                    UNION ALL SELECT 'accessible_person'
                    WHERE EXISTS (
                        SELECT 1 FROM asset.agent_accessible_person access
                        WHERE access.agent_id = agent.agent_id
                          AND access.person_id = %(person_id)s
                          AND access.valid_to IS NULL
                    )
                ) relationship ON TRUE
                ORDER BY agent.name, relationship.role, agent.agent_id
                """,
                {"person_id": person_id},
            ).fetchall())
            item["change_log"] = list(connection.execute(
                """
                SELECT change_id, occurred_at, actor_type, actor_ref, source,
                       operation::text AS operation, changed_fields,
                       before_value, after_value, reason
                FROM audit.change_log
                WHERE object_type = 'person' AND object_id = %(person_id)s
                ORDER BY occurred_at DESC, change_id DESC
                LIMIT 50
                """,
                {"person_id": person_id},
            ).fetchall())
            return item

    def departments(self, limit, offset, is_active=None, parent_department_id=None, q=None):
        select_sql = """
            SELECT d.department_id AS stable_id, d.department_id, d.name,
                   d.department_code, d.parent_department_id,
                   parent.department_code AS parent_department_code,
                   parent.name AS parent_department_name,
                   d.is_active,
                   (SELECT count(*) FROM org.department child
                    WHERE child.parent_department_id = d.department_id
                      AND child.is_active) AS active_child_count,
                   (SELECT count(*) FROM org.person person
                    WHERE person.primary_department_id = d.department_id
                      AND person.employment_status = 'active') AS active_person_count,
                   (SELECT count(*) FROM asset.agent_asset agent
                    WHERE agent.lifecycle_status <> 'retired'
                      AND (agent.owning_department_id = d.department_id
                           OR agent.primary_service_department_id = d.department_id))
                       AS active_agent_references,
                   d.row_version, d.created_at, d.updated_at
            FROM org.department d
            LEFT JOIN org.department parent
              ON parent.department_id = d.parent_department_id
        """
        count_sql = """
            SELECT count(*) AS total
            FROM org.department d
            LEFT JOIN org.department parent
              ON parent.department_id = d.parent_department_id
        """
        clauses = []
        params = {}
        add_filter(clauses, params, is_active, "d.is_active = %(is_active)s", "is_active")
        add_filter(
            clauses, params, parent_department_id,
            "d.parent_department_id = %(parent_department_id)s", "parent_department_id",
        )
        if q:
            clauses.append("(d.name ILIKE %(q)s OR d.department_code ILIKE %(q)s)")
            params["q"] = f"%{q}%"
        with self.database.connection() as connection:
            return paginated(connection, select_sql, count_sql, clauses, params, limit, offset)

    def department(self, department_id: UUID):
        with self.database.connection() as connection:
            item = connection.execute(
                """
                SELECT d.department_id, d.department_code, d.name,
                       d.parent_department_id,
                       parent.department_code AS parent_department_code,
                       parent.name AS parent_department_name,
                       d.is_active, d.row_version, d.created_at, d.updated_at
                FROM org.department d
                LEFT JOIN org.department parent
                  ON parent.department_id = d.parent_department_id
                WHERE d.department_id = %(department_id)s
                """,
                {"department_id": department_id},
            ).fetchone()
            if item is None:
                return None
            item["children"] = list(connection.execute(
                """
                SELECT department_id, department_code, name, is_active, row_version
                FROM org.department
                WHERE parent_department_id = %(department_id)s
                ORDER BY name, department_id
                """,
                {"department_id": department_id},
            ).fetchall())
            item["people"] = list(connection.execute(
                """
                SELECT person_id, employee_no, display_name,
                       employment_status, row_version
                FROM org.person
                WHERE primary_department_id = %(department_id)s
                ORDER BY display_name, person_id
                """,
                {"department_id": department_id},
            ).fetchall())
            item["agent_relationships"] = list(connection.execute(
                """
                SELECT agent_id, name,
                       structure_role::text AS structure_role,
                       lifecycle_status::text AS lifecycle_status,
                       CASE
                           WHEN owning_department_id = %(department_id)s
                                AND primary_service_department_id = %(department_id)s
                               THEN 'owner_and_service_target'
                           WHEN owning_department_id = %(department_id)s THEN 'owner'
                           ELSE 'service_target'
                       END AS role
                FROM asset.agent_asset
                WHERE owning_department_id = %(department_id)s
                   OR primary_service_department_id = %(department_id)s
                ORDER BY name, agent_id
                """,
                {"department_id": department_id},
            ).fetchall())
            item["change_log"] = list(connection.execute(
                """
                SELECT change_id, occurred_at, actor_type, actor_ref, source,
                       operation::text AS operation, changed_fields,
                       before_value, after_value, reason
                FROM audit.change_log
                WHERE object_type = 'department' AND object_id = %(department_id)s
                ORDER BY occurred_at DESC, change_id DESC
                LIMIT 50
                """,
                {"department_id": department_id},
            ).fetchall())
            return item

    def servers(self, limit, offset, lifecycle_status=None, environment_code=None, group_code=None, q=None):
        select_sql = """
            SELECT s.server_id AS stable_id, s.server_id, s.name,
                   s.lifecycle_status::text AS lifecycle_status,
                   e.environment_code, cp.provider_code, s.provider_instance_id,
                   s.cpu_cores, s.memory_gib, s.disk_gib,
                   host(s.internal_ip) AS internal_ip, host(s.public_ip) AS public_ip,
                   s.os_name, s.os_version, s.architecture, s.work_account,
                   sc.connection_status::text AS connection_status,
                   sc.last_checked_at, sc.last_facts_at, sc.last_runtime_discovery_at,
                   s.row_version, s.updated_at
            FROM asset.server_asset s
            JOIN asset.environment e ON e.environment_id = s.environment_id
            LEFT JOIN asset.cloud_provider cp ON cp.cloud_provider_id = s.cloud_provider_id
            LEFT JOIN asset.server_connection sc ON sc.server_id = s.server_id
        """
        count_sql = """
            SELECT count(*) AS total
            FROM asset.server_asset s
            JOIN asset.environment e ON e.environment_id = s.environment_id
            LEFT JOIN asset.cloud_provider cp ON cp.cloud_provider_id = s.cloud_provider_id
            LEFT JOIN asset.server_connection sc ON sc.server_id = s.server_id
        """
        clauses = []
        params = {}
        add_filter(clauses, params, lifecycle_status, "s.lifecycle_status = %(lifecycle_status)s", "lifecycle_status")
        add_filter(clauses, params, environment_code, "e.environment_code = %(environment_code)s", "environment_code")
        if group_code:
            clauses.append(
                "EXISTS (SELECT 1 FROM asset.server_group_member sgm "
                "JOIN asset.management_group mg ON mg.group_id = sgm.group_id "
                "WHERE sgm.server_id = s.server_id AND sgm.valid_to IS NULL AND mg.group_code = %(group_code)s)"
            )
            params["group_code"] = group_code
        if q:
            clauses.append(
                "(s.name ILIKE %(q)s OR s.provider_instance_id ILIKE %(q)s "
                "OR host(s.internal_ip) ILIKE %(q)s OR host(s.public_ip) ILIKE %(q)s)"
            )
            params["q"] = f"%{q}%"
        with self.database.connection() as connection:
            return paginated(connection, select_sql, count_sql, clauses, params, limit, offset)

    def server(self, server_id: UUID):
        with self.database.connection() as connection:
            item = connection.execute(
                """
                SELECT s.server_id, s.name, s.lifecycle_status::text AS lifecycle_status,
                       s.environment_id, s.cloud_provider_id,
                       e.environment_code, cp.provider_code, s.provider_instance_id,
                       s.cpu_cores, s.memory_gib, s.disk_gib,
                       host(s.internal_ip) AS internal_ip, host(s.public_ip) AS public_ip,
                       s.os_name, s.os_version, s.architecture, s.work_account,
                       s.row_version, s.created_at, s.updated_at
                FROM asset.server_asset s
                JOIN asset.environment e ON e.environment_id = s.environment_id
                LEFT JOIN asset.cloud_provider cp ON cp.cloud_provider_id = s.cloud_provider_id
                WHERE s.server_id = %(server_id)s
                """,
                {"server_id": server_id},
            ).fetchone()
            if item is None:
                return None
            item["runtimes"] = list(
                connection.execute(
                    """
                    SELECT r.runtime_id, r.name, rf.family_code,
                           r.deployment_mode::text AS deployment_mode,
                           r.lifecycle_status::text AS lifecycle_status,
                           r.observed_status::text AS observed_status,
                           r.endpoint_port
                    FROM asset.runtime_instance r
                    JOIN asset.runtime_family rf ON rf.runtime_family_id = r.runtime_family_id
                    WHERE r.server_id = %(server_id)s
                    ORDER BY r.name, r.runtime_id
                    """,
                    {"server_id": server_id},
                ).fetchall()
            )
            item["groups"] = list(
                connection.execute(
                    """
                    SELECT mg.group_id, mg.group_code, mg.name, sgm.valid_from
                    FROM asset.server_group_member sgm
                    JOIN asset.management_group mg ON mg.group_id = sgm.group_id
                    WHERE sgm.server_id = %(server_id)s AND sgm.valid_to IS NULL
                    ORDER BY mg.name, mg.group_id
                    """,
                    {"server_id": server_id},
                ).fetchall()
            )
            item["connection"] = connection.execute(
                """
                SELECT connection_id, connection_type::text AS connection_type,
                       host, port, username, credential_ref, use_become,
                       password_configured,
                       ssh_host_keys, ssh_host_key_fingerprints,
                       bootstrap_status::text AS bootstrap_status,
                       bootstrap_error, bootstrap_completed_at, password_updated_at,
                       connection_status::text AS connection_status,
                       last_checked_at, last_error, last_facts, last_facts_at,
                       last_runtime_discovery, last_runtime_discovery_at,
                       row_version, created_at, updated_at
                FROM asset.server_connection
                WHERE server_id = %(server_id)s
                """,
                {"server_id": server_id},
            ).fetchone()
            item["recent_operations"] = list(
                connection.execute(
                    """
                    SELECT r.run_id, r.operation_code, r.status::text AS run_status,
                           h.status::text AS host_status, h.result, h.error_message,
                           r.requested_by, r.created_at, h.started_at, h.finished_at
                    FROM automation.operation_host_result h
                    JOIN automation.operation_run r ON r.run_id = h.run_id
                    WHERE h.server_id = %(server_id)s
                    ORDER BY r.created_at DESC, r.run_id DESC
                    LIMIT 10
                    """,
                    {"server_id": server_id},
                ).fetchall()
            )
            return item

    def operation_run(self, run_id: UUID):
        with self.database.connection() as connection:
            item = connection.execute(
                """
                SELECT run.run_id, run.operation_code, run.target_type,
                       run.status::text AS status, run.parameters,
                       run.requested_by, run.target_count, run.succeeded_count, run.failed_count,
                       run.playbook_version, run.automation_version_id, run.retry_of_run_id,
                       run.cancellation_requested_at, run.cancellation_requested_by,
                       run.created_at, run.started_at, run.finished_at,
                       definition.name AS operation_name, definition.category,
                       definition.risk_level, version.version_label
                FROM automation.operation_run run
                LEFT JOIN automation.automation_version version
                  ON version.automation_version_id = run.automation_version_id
                LEFT JOIN automation.automation_definition definition
                  ON definition.automation_id = version.automation_id
                WHERE run.run_id = %(run_id)s
                """,
                {"run_id": run_id},
            ).fetchone()
            if item is None:
                return None
            item["hosts"] = list(
                connection.execute(
                    """
                    SELECT h.host_result_id, h.server_id, s.name AS server_name,
                           h.runtime_id, r.name AS runtime_name,
                           h.agent_id, a.name AS agent_name, h.agent_deployment_id,
                           h.status::text AS status, h.target_plan, h.result, h.error_message,
                           h.started_at, h.finished_at
                    FROM automation.operation_host_result h
                    JOIN asset.server_asset s ON s.server_id = h.server_id
                    LEFT JOIN asset.runtime_instance r ON r.runtime_id = h.runtime_id
                    LEFT JOIN asset.agent_asset a ON a.agent_id = h.agent_id
                    WHERE h.run_id = %(run_id)s
                    ORDER BY COALESCE(a.name, r.name, s.name), h.target_key
                    """,
                    {"run_id": run_id},
                ).fetchall()
            )
            event_summary = connection.execute(
                """
                SELECT count(*) AS event_count,
                       COALESCE(max(sequence_no), 0) AS last_event_sequence
                FROM automation.operation_event
                WHERE run_id = %(run_id)s
                """,
                {"run_id": run_id},
            ).fetchone()
            item.update(event_summary)
            item["log_available"] = event_summary["event_count"] > 0
            return item

    def operation_events(self, run_id: UUID, after_sequence=0, limit=200, target_key=None):
        clauses = ["run_id = %(run_id)s", "sequence_no > %(after_sequence)s"]
        params = {
            "run_id": run_id,
            "after_sequence": after_sequence,
            "limit": limit,
        }
        if target_key:
            clauses.append("target_key = %(target_key)s")
            params["target_key"] = target_key
        where = " AND ".join(clauses)
        with self.database.connection() as connection:
            exists = connection.execute(
                "SELECT status::text AS status FROM automation.operation_run WHERE run_id = %(run_id)s",
                {"run_id": run_id},
            ).fetchone()
            if exists is None:
                return None
            rows = list(connection.execute(
                f"""
                SELECT operation_event_id, run_id, sequence_no, phase, event_type,
                       event_level, target_key, play_name, task_name, task_action,
                       changed, stdout, event_data, occurred_at
                FROM automation.operation_event
                WHERE {where}
                ORDER BY sequence_no
                LIMIT %(limit)s
                """,
                params,
            ).fetchall())
            last_sequence = rows[-1]["sequence_no"] if rows else after_sequence
            remaining = connection.execute(
                f"""
                SELECT count(*) AS count FROM automation.operation_event
                WHERE {where} AND sequence_no > %(last_sequence)s
                """,
                {**params, "last_sequence": last_sequence},
            ).fetchone()["count"]
        return {
            "data": rows,
            "cursor": {
                "after_sequence": after_sequence,
                "next_sequence": last_sequence,
                "has_more": remaining > 0,
                "terminal": exists["status"] not in {"queued", "running"},
            },
        }

    def operation_runs(self, limit, offset, status=None, target_type=None, operation_code=None,
                       requested_by=None, q=None):
        clauses = []
        params = {"limit": limit, "offset": offset}
        add_filter(clauses, params, status, "run.status = %(status)s", "status")
        add_filter(clauses, params, target_type, "run.target_type = %(target_type)s", "target_type")
        add_filter(clauses, params, operation_code, "run.operation_code = %(operation_code)s", "operation_code")
        add_filter(clauses, params, requested_by, "run.requested_by = %(requested_by)s", "requested_by")
        if q:
            clauses.append(
                """
                (run.run_id::text ILIKE %(q)s OR run.operation_code ILIKE %(q)s
                 OR run.requested_by ILIKE %(q)s OR definition.name ILIKE %(q)s
                 OR EXISTS (
                     SELECT 1 FROM automation.operation_host_result target
                     JOIN asset.server_asset server ON server.server_id = target.server_id
                     LEFT JOIN asset.runtime_instance runtime ON runtime.runtime_id = target.runtime_id
                     LEFT JOIN asset.agent_asset agent ON agent.agent_id = target.agent_id
                     WHERE target.run_id = run.run_id
                       AND COALESCE(agent.name, runtime.name, server.name) ILIKE %(q)s
                 ))
                """
            )
            params["q"] = f"%{q}%"
        where = " AND ".join(clauses) if clauses else "TRUE"
        with self.database.connection() as connection:
            total = connection.execute(
                f"""
                SELECT count(*) AS total
                FROM automation.operation_run run
                LEFT JOIN automation.automation_version version
                  ON version.automation_version_id = run.automation_version_id
                LEFT JOIN automation.automation_definition definition
                  ON definition.automation_id = version.automation_id
                WHERE {where}
                """,
                params,
            ).fetchone()["total"]
            rows = list(connection.execute(
                f"""
                SELECT run.run_id, run.operation_code, run.target_type,
                       run.status::text AS status, run.requested_by,
                       run.target_count, run.succeeded_count, run.failed_count,
                       run.playbook_version, run.automation_version_id, run.retry_of_run_id,
                       run.created_at, run.started_at, run.finished_at,
                       definition.name AS operation_name, definition.category,
                       definition.risk_level, version.version_label,
                       (SELECT COALESCE(jsonb_agg(target_name ORDER BY target_name), '[]'::jsonb)
                        FROM (
                            SELECT COALESCE(agent.name, runtime.name, server.name) AS target_name
                            FROM automation.operation_host_result result
                            JOIN asset.server_asset server ON server.server_id = result.server_id
                            LEFT JOIN asset.runtime_instance runtime ON runtime.runtime_id = result.runtime_id
                            LEFT JOIN asset.agent_asset agent ON agent.agent_id = result.agent_id
                            WHERE result.run_id = run.run_id
                            ORDER BY result.target_key LIMIT 4
                        ) names) AS target_names
                FROM automation.operation_run run
                LEFT JOIN automation.automation_version version
                  ON version.automation_version_id = run.automation_version_id
                LEFT JOIN automation.automation_definition definition
                  ON definition.automation_id = version.automation_id
                WHERE {where}
                ORDER BY run.created_at DESC, run.run_id DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            ).fetchall())
            summary = list(connection.execute(
                """
                SELECT status::text AS status, count(*) AS count
                FROM automation.operation_run GROUP BY status ORDER BY status
                """
            ).fetchall())
        return {
            "data": rows,
            "page": {"limit": limit, "offset": offset, "total": total},
            "summary": {row["status"]: row["count"] for row in summary},
        }

    def runtimes(self, limit, offset, lifecycle_status=None, family_code=None,
                 deployment_mode=None, server_id=None, group_code=None, q=None):
        select_sql = """
            SELECT r.runtime_id AS stable_id, r.runtime_id, r.name, rf.family_code,
                   r.runtime_role::text AS runtime_role,
                   r.deployment_mode::text AS deployment_mode,
                   r.lifecycle_status::text AS lifecycle_status,
                   r.observed_status::text AS observed_status,
                   r.management_driver::text AS management_driver, r.management_target,
                   r.command_path, r.config_path, r.state_path,
                   r.endpoint_port, r.desired_version, r.last_discovered_at,
                   s.server_id, s.name AS server_name,
                   s.lifecycle_status::text AS server_lifecycle_status,
                   COALESCE(NULLIF(btrim(s.provider_instance_id), ''), s.name) AS server_display_id,
                   host(s.internal_ip) AS server_internal_ip,
                   CASE
                       WHEN rf.family_code <> 'openclaw'
                           THEN false
                       WHEN r.deployment_mode <> 'baremetal'
                           THEN false
                       WHEN r.lifecycle_status <> 'active'
                           THEN false
                       WHEN s.lifecycle_status NOT IN ('active', 'maintenance')
                           THEN false
                       WHEN NULLIF(btrim(r.config_path), '') IS NULL
                         OR NULLIF(btrim(r.state_path), '') IS NULL
                           THEN false
                       WHEN r.management_driver NOT IN ('systemd', 'systemd_user', 'launchd', 'mock')
                         OR NULLIF(btrim(r.management_target), '') IS NULL
                           THEN false
                       WHEN sc.server_id IS NULL
                           THEN false
                       WHEN sc.connection_type = 'ssh'
                        AND sc.password_configured
                        AND sc.bootstrap_status <> 'succeeded'
                           THEN false
                       ELSE true
                   END AS deployment_eligible,
                   CASE
                       WHEN rf.family_code <> 'openclaw'
                           THEN '仅裸金属 OpenClaw 运行时可用于智能体部署'
                       WHEN r.deployment_mode <> 'baremetal'
                           THEN '当前不是裸金属运行时，不能承载此部署流程'
                       WHEN r.lifecycle_status <> 'active'
                           THEN '运行时未处于活动状态'
                       WHEN s.lifecycle_status NOT IN ('active', 'maintenance')
                           THEN '宿主服务器未处于活动或维护状态'
                       WHEN NULLIF(btrim(r.config_path), '') IS NULL
                         OR NULLIF(btrim(r.state_path), '') IS NULL
                           THEN '运行时缺少配置路径或状态目录'
                       WHEN r.management_driver NOT IN ('systemd', 'systemd_user', 'launchd', 'mock')
                         OR NULLIF(btrim(r.management_target), '') IS NULL
                           THEN '运行时尚未完成服务管理配置'
                       WHEN sc.server_id IS NULL
                           THEN '宿主服务器尚未配置 Ansible 连接'
                       WHEN sc.connection_type = 'ssh'
                        AND sc.password_configured
                        AND sc.bootstrap_status <> 'succeeded'
                           THEN '宿主服务器尚未完成密码建联和公钥复验'
                       ELSE NULL
                   END AS deployment_blocked_reason,
                   gateway.identifier_value AS gateway_id,
                   active_deployment.primary_agent_id,
                   active_deployment.lobster_name,
                   CASE WHEN active_deployment.primary_agent_id IS NULL THEN 0 ELSE 1 END AS active_primary_agents,
                   EXISTS (
                       SELECT 1 FROM asset.runtime_web_endpoint web
                       WHERE web.runtime_id = r.runtime_id AND web.enabled AND web.is_primary
                   ) AS web_ui_enabled,
                   r.row_version, r.updated_at
            FROM asset.runtime_instance r
            JOIN asset.runtime_family rf ON rf.runtime_family_id = r.runtime_family_id
            JOIN asset.server_asset s ON s.server_id = r.server_id
            LEFT JOIN asset.server_connection sc ON sc.server_id = s.server_id
            LEFT JOIN LATERAL (
                SELECT ad.primary_agent_id, agent.name AS lobster_name
                FROM asset.agent_deployment ad
                JOIN asset.agent_asset agent ON agent.agent_id = ad.primary_agent_id
                WHERE ad.runtime_id = r.runtime_id AND ad.lifecycle_status = 'active'
                LIMIT 1
            ) active_deployment ON true
            LEFT JOIN LATERAL (
                SELECT identifier.identifier_value
                FROM asset.external_identifier identifier
                WHERE identifier.runtime_id = r.runtime_id
                  AND identifier.valid_to IS NULL
                  AND identifier.identifier_type IN ('gateway_id', 'legacy_runtime_id')
                ORDER BY
                    CASE identifier.identifier_type WHEN 'gateway_id' THEN 0 ELSE 1 END,
                    identifier.valid_from DESC,
                    identifier.created_at DESC,
                    identifier.external_identifier_id
                LIMIT 1
            ) gateway ON true
        """
        count_sql = """
            SELECT count(*) AS total
            FROM asset.runtime_instance r
            JOIN asset.runtime_family rf ON rf.runtime_family_id = r.runtime_family_id
            JOIN asset.server_asset s ON s.server_id = r.server_id
        """
        clauses = []
        params = {}
        add_filter(clauses, params, lifecycle_status, "r.lifecycle_status = %(lifecycle_status)s", "lifecycle_status")
        add_filter(clauses, params, family_code, "rf.family_code = %(family_code)s", "family_code")
        add_filter(clauses, params, deployment_mode, "r.deployment_mode = %(deployment_mode)s", "deployment_mode")
        add_filter(clauses, params, server_id, "r.server_id = %(server_id)s", "server_id")
        if group_code:
            clauses.append(
                "EXISTS (SELECT 1 FROM asset.runtime_group_member rgm "
                "JOIN asset.management_group mg ON mg.group_id = rgm.group_id "
                "WHERE rgm.runtime_id = r.runtime_id AND rgm.valid_to IS NULL "
                "AND mg.lifecycle_status = 'active' AND mg.group_code = %(group_code)s)"
            )
            params["group_code"] = group_code
        if q:
            clauses.append("(r.name ILIKE %(q)s OR s.name ILIKE %(q)s)")
            params["q"] = f"%{q}%"
        with self.database.connection() as connection:
            return paginated(connection, select_sql, count_sql, clauses, params, limit, offset)

    def runtime(self, runtime_id: UUID):
        with self.database.connection() as connection:
            item = connection.execute(
                """
                SELECT r.runtime_id, r.name, r.runtime_family_id, rf.family_code,
                       r.runtime_role::text AS runtime_role,
                       r.deployment_mode::text AS deployment_mode,
                       r.lifecycle_status::text AS lifecycle_status,
                       r.observed_status::text AS observed_status,
                       r.bind_address, r.endpoint_port, r.runtime_account,
                       r.config_path, r.state_path, r.command_path,
                       r.container_name, r.image_ref,
                       r.desired_version, r.last_discovered_at,
                       r.management_driver::text AS management_driver,
                       r.management_target, r.last_operation_at,
                       s.server_id, s.name AS server_name, host(s.internal_ip) AS server_internal_ip,
                       sc.connection_status::text AS server_connection_status,
                       r.row_version, r.created_at, r.updated_at
                FROM asset.runtime_instance r
                JOIN asset.runtime_family rf ON rf.runtime_family_id = r.runtime_family_id
                JOIN asset.server_asset s ON s.server_id = r.server_id
                LEFT JOIN asset.server_connection sc ON sc.server_id = s.server_id
                WHERE r.runtime_id = %(runtime_id)s
                """,
                {"runtime_id": runtime_id},
            ).fetchone()
            if item is None:
                return None
            item["active_deployments"] = list(
                connection.execute(
                    """
                    SELECT ad.agent_deployment_id, a.agent_id, a.name AS agent_name,
                           a.service_archetype::text AS service_archetype,
                           ad.valid_from
                    FROM asset.agent_deployment ad
                    JOIN asset.agent_asset a ON a.agent_id = ad.primary_agent_id
                    WHERE ad.runtime_id = %(runtime_id)s AND ad.lifecycle_status = 'active'
                    ORDER BY a.name, a.agent_id
                    """,
                    {"runtime_id": runtime_id},
                ).fetchall()
            )
            item["groups"] = list(
                connection.execute(
                    """
                    SELECT mg.group_id, mg.group_code, mg.name, rgm.valid_from
                    FROM asset.runtime_group_member rgm
                    JOIN asset.management_group mg ON mg.group_id = rgm.group_id
                    WHERE rgm.runtime_id = %(runtime_id)s AND rgm.valid_to IS NULL
                    ORDER BY mg.name, mg.group_id
                    """,
                    {"runtime_id": runtime_id},
                ).fetchall()
            )
            item["latest_snapshot"] = connection.execute(
                """
                SELECT snapshot_id, discovered_at, discovery_status::text AS discovery_status,
                       actual_version, observed_agent_count, agent_count_confidence, content_hash,
                       extension_summary, config_summary
                FROM asset.runtime_discovery_snapshot
                WHERE runtime_id = %(runtime_id)s
                ORDER BY discovered_at DESC, snapshot_id DESC LIMIT 1
                """,
                {"runtime_id": runtime_id},
            ).fetchone()
            item["recent_snapshots"] = list(
                connection.execute(
                    """
                    SELECT snapshot_id, discovered_at, discovery_status::text AS discovery_status,
                           actual_version, observed_agent_count, agent_count_confidence,
                           content_hash, extension_summary, config_summary
                    FROM asset.runtime_discovery_snapshot
                    WHERE runtime_id = %(runtime_id)s
                    ORDER BY discovered_at DESC, snapshot_id DESC
                    LIMIT 10
                    """,
                    {"runtime_id": runtime_id},
                ).fetchall()
            )
            item["recent_operations"] = list(
                connection.execute(
                    """
                    SELECT run.run_id, run.operation_code, run.status::text AS run_status,
                           result.status::text AS host_status, result.result,
                           result.error_message, run.requested_by, run.created_at,
                           result.started_at, result.finished_at
                    FROM automation.operation_host_result result
                    JOIN automation.operation_run run ON run.run_id = result.run_id
                    WHERE result.runtime_id = %(runtime_id)s
                    ORDER BY run.created_at DESC, run.run_id DESC
                    LIMIT 10
                    """,
                    {"runtime_id": runtime_id},
                ).fetchall()
            )
            item["web_endpoint"] = connection.execute(
                """
                SELECT web.runtime_web_endpoint_id, web.runtime_id, web.endpoint_code,
                       web.display_name, web.access_mode::text AS access_mode,
                       web.scheme, web.direct_url, web.remote_host, web.port_override,
                       COALESCE(web.port_override, r.endpoint_port) AS effective_port,
                       web.base_path, web.auth_mode::text AS auth_mode,
                       (web.encrypted_credential IS NOT NULL) AS credential_configured,
                       web.enabled, web.is_primary, web.last_opened_at,
                       web.row_version, web.created_at, web.updated_at
                FROM asset.runtime_web_endpoint web
                JOIN asset.runtime_instance r ON r.runtime_id = web.runtime_id
                WHERE web.runtime_id = %(runtime_id)s AND web.is_primary
                ORDER BY web.created_at DESC
                LIMIT 1
                """,
                {"runtime_id": runtime_id},
            ).fetchone()
            item["recent_web_access_sessions"] = list(
                connection.execute(
                    """
                    SELECT access_session_id, status::text AS status, access_mode::text AS access_mode,
                           requested_by, created_at, activated_at, expires_at, closed_at,
                           failure_reason
                    FROM automation.runtime_web_access_session
                    WHERE runtime_id = %(runtime_id)s
                    ORDER BY created_at DESC, access_session_id DESC
                    LIMIT 5
                    """,
                    {"runtime_id": runtime_id},
                ).fetchall()
            )
            return item

    def runtime_web_endpoint(self, runtime_id: UUID):
        with self.database.connection() as connection:
            return connection.execute(
                """
                SELECT web.runtime_web_endpoint_id, web.runtime_id, web.endpoint_code,
                       web.display_name, web.access_mode::text AS access_mode,
                       web.scheme, web.direct_url, web.remote_host, web.port_override,
                       COALESCE(web.port_override, r.endpoint_port) AS effective_port,
                       web.base_path, web.auth_mode::text AS auth_mode,
                       (web.encrypted_credential IS NOT NULL) AS credential_configured,
                       web.enabled, web.is_primary, web.last_opened_at,
                       web.row_version, web.created_at, web.updated_at
                FROM asset.runtime_web_endpoint web
                JOIN asset.runtime_instance r ON r.runtime_id = web.runtime_id
                WHERE web.runtime_id = %(runtime_id)s AND web.is_primary
                ORDER BY web.created_at DESC
                LIMIT 1
                """,
                {"runtime_id": runtime_id},
            ).fetchone()

    def agents(self, limit, offset, structure_role=None, service_archetype=None, lifecycle_status=None, department_code=None, group_code=None, q=None):
        select_sql = """
            SELECT a.agent_id AS stable_id, a.agent_id, a.name,
                   a.structure_role::text AS structure_role,
                   a.service_archetype::text AS service_archetype,
                   a.lifecycle_status::text AS lifecycle_status,
                   d.department_code AS owning_department_code, d.name AS owning_department_name,
                   parent.agent_id AS parent_agent_id, parent.name AS parent_agent_name,
                   ba.display_name AS business_admin_name,
                   ia.display_name AS it_admin_name,
                   CASE WHEN channel.enabled THEN channel.channel_type::text ELSE 'none' END AS channel_type,
                   ad.agent_deployment_id AS active_deployment_id,
                   r.runtime_id AS active_runtime_id, r.name AS active_runtime_name,
                   a.row_version, a.updated_at
            FROM asset.agent_asset a
            LEFT JOIN org.department d ON d.department_id = a.owning_department_id
            LEFT JOIN asset.agent_asset parent ON parent.agent_id = a.parent_agent_id
            LEFT JOIN org.person ba ON ba.person_id = a.business_admin_person_id
            LEFT JOIN org.person ia ON ia.person_id = a.it_admin_person_id
            LEFT JOIN asset.agent_channel_configuration channel ON channel.agent_id = a.agent_id
            LEFT JOIN asset.agent_deployment ad
                   ON ad.primary_agent_id = a.agent_id AND ad.lifecycle_status = 'active'
            LEFT JOIN asset.runtime_instance r ON r.runtime_id = ad.runtime_id
        """
        count_sql = """
            SELECT count(*) AS total
            FROM asset.agent_asset a
            LEFT JOIN org.department d ON d.department_id = a.owning_department_id
            LEFT JOIN asset.agent_asset parent ON parent.agent_id = a.parent_agent_id
            LEFT JOIN org.person ba ON ba.person_id = a.business_admin_person_id
            LEFT JOIN org.person ia ON ia.person_id = a.it_admin_person_id
            LEFT JOIN asset.agent_channel_configuration channel ON channel.agent_id = a.agent_id
            LEFT JOIN asset.agent_deployment ad
                   ON ad.primary_agent_id = a.agent_id AND ad.lifecycle_status = 'active'
            LEFT JOIN asset.runtime_instance r ON r.runtime_id = ad.runtime_id
        """
        clauses = []
        params = {}
        add_filter(clauses, params, structure_role, "a.structure_role = %(structure_role)s", "structure_role")
        add_filter(clauses, params, service_archetype, "a.service_archetype = %(service_archetype)s", "service_archetype")
        add_filter(clauses, params, lifecycle_status, "a.lifecycle_status = %(lifecycle_status)s", "lifecycle_status")
        add_filter(clauses, params, department_code, "d.department_code = %(department_code)s", "department_code")
        if group_code:
            clauses.append(
                "EXISTS (SELECT 1 FROM asset.agent_group_member agm "
                "JOIN asset.management_group mg ON mg.group_id = agm.group_id "
                "WHERE agm.agent_id IN (a.agent_id, a.parent_agent_id) "
                "AND agm.valid_to IS NULL AND mg.lifecycle_status = 'active' "
                "AND mg.group_code = %(group_code)s)"
            )
            params["group_code"] = group_code
        if q:
            clauses.append("(a.name ILIKE %(q)s OR d.name ILIKE %(q)s)")
            params["q"] = f"%{q}%"
        with self.database.connection() as connection:
            return paginated(connection, select_sql, count_sql, clauses, params, limit, offset)

    def agent(self, agent_id: UUID):
        with self.database.connection() as connection:
            item = connection.execute(
                """
                SELECT a.agent_id, a.name, a.structure_role::text AS structure_role,
                       a.service_archetype::text AS service_archetype,
                       a.lifecycle_status::text AS lifecycle_status,
                       a.owning_department_id,
                       d.department_code AS owning_department_code, d.name AS owning_department_name,
                       parent.agent_id AS parent_agent_id, parent.name AS parent_agent_name,
                       a.business_admin_person_id, a.it_admin_person_id,
                       ba.employee_no AS business_admin_employee_no, ba.display_name AS business_admin_name,
                       ia.employee_no AS it_admin_employee_no, ia.display_name AS it_admin_name,
                       a.primary_service_person_id, a.primary_service_department_id,
                       sp.employee_no AS primary_service_employee_no, sp.display_name AS primary_service_person_name,
                       sd.department_code AS primary_service_department_code,
                       a.business_domain, a.business_scenario, a.description,
                       a.row_version, a.created_at, a.updated_at
                FROM asset.agent_asset a
                LEFT JOIN org.department d ON d.department_id = a.owning_department_id
                LEFT JOIN asset.agent_asset parent ON parent.agent_id = a.parent_agent_id
                LEFT JOIN org.person ba ON ba.person_id = a.business_admin_person_id
                LEFT JOIN org.person ia ON ia.person_id = a.it_admin_person_id
                LEFT JOIN org.person sp ON sp.person_id = a.primary_service_person_id
                LEFT JOIN org.department sd ON sd.department_id = a.primary_service_department_id
                WHERE a.agent_id = %(agent_id)s
                """,
                {"agent_id": agent_id},
            ).fetchone()
            if item is None:
                return None
            channel = connection.execute(
                """
                SELECT agent_channel_configuration_id, channel_type::text AS channel_type,
                       application_name, app_id,
                       (encrypted_app_secret IS NOT NULL) AS credential_configured,
                       enabled, row_version, created_at, updated_at
                FROM asset.agent_channel_configuration
                WHERE agent_id = %(agent_id)s AND enabled
                """,
                {"agent_id": item["agent_id"]},
            ).fetchone()
            item["channel"] = (
                dict(channel) if channel else {"channel_type": "none"}
            )
            item["subagents"] = list(
                connection.execute(
                    """
                    SELECT agent_id, name, lifecycle_status::text AS lifecycle_status
                    FROM asset.agent_asset
                    WHERE parent_agent_id = %(agent_id)s
                    ORDER BY name, agent_id
                    """,
                    {"agent_id": agent_id},
                ).fetchall()
            )
            item["accessible_people"] = list(
                connection.execute(
                    """
                    SELECT p.person_id, p.employee_no, p.display_name, ap.valid_from
                    FROM asset.agent_accessible_person ap
                    JOIN org.person p ON p.person_id = ap.person_id
                    WHERE ap.agent_id = %(agent_id)s AND ap.valid_to IS NULL
                    ORDER BY p.display_name, p.person_id
                    """,
                    {"agent_id": agent_id},
                ).fetchall()
            )
            item["groups"] = list(
                connection.execute(
                    """
                    SELECT mg.group_id, mg.group_code, mg.name, agm.valid_from
                    FROM asset.agent_group_member agm
                    JOIN asset.management_group mg ON mg.group_id = agm.group_id
                    WHERE agm.agent_id = %(agent_id)s AND agm.valid_to IS NULL
                    ORDER BY mg.name, mg.group_id
                    """,
                    {"agent_id": agent_id},
                ).fetchall()
            )
            item["active_deployment"] = connection.execute(
                """
                SELECT ad.agent_deployment_id, ad.runtime_id, r.name AS runtime_name,
                       rf.family_code, ad.valid_from
                FROM asset.agent_deployment ad
                JOIN asset.runtime_instance r ON r.runtime_id = ad.runtime_id
                JOIN asset.runtime_family rf ON rf.runtime_family_id = r.runtime_family_id
                WHERE ad.primary_agent_id = %(agent_id)s AND ad.lifecycle_status = 'active'
                """,
                {"agent_id": agent_id},
            ).fetchone()
            item["deployment_members"] = []
            if item["active_deployment"]:
                item["deployment_members"] = list(
                    connection.execute(
                        """
                        SELECT member.deployment_member_id, member.agent_id, agent.name,
                               agent.lifecycle_status::text AS agent_lifecycle_status,
                               member.member_role::text AS member_role,
                               member.runtime_agent_key,
                               member.mapping_status::text AS mapping_status,
                               member.config_ref, member.valid_from
                        FROM asset.agent_deployment_member member
                        JOIN asset.agent_asset agent ON agent.agent_id = member.agent_id
                        WHERE member.agent_deployment_id = %(deployment_id)s
                          AND member.valid_to IS NULL
                        ORDER BY CASE WHEN member.member_role = 'primary' THEN 0 ELSE 1 END,
                                 agent.name, member.deployment_member_id
                        """,
                        {"deployment_id": item["active_deployment"]["agent_deployment_id"]},
                    ).fetchall()
                )
            item["recent_operations"] = list(
                connection.execute(
                    """
                    SELECT run.run_id, run.operation_code, run.status::text AS run_status,
                           result.status::text AS host_status, result.result,
                           result.error_message, run.requested_by, run.created_at,
                           result.started_at, result.finished_at
                    FROM automation.operation_host_result result
                    JOIN automation.operation_run run ON run.run_id = result.run_id
                    WHERE result.agent_id = %(agent_id)s
                    ORDER BY run.created_at DESC, run.run_id DESC
                    LIMIT 10
                    """,
                    {"agent_id": agent_id},
                ).fetchall()
            )
            item["network_requirements"] = list(
                connection.execute(
                    """
                    SELECT network_requirement_id, system_name,
                           target_type::text AS target_type, target_value,
                           protocol::text AS protocol, port_from, port_to, purpose
                    FROM asset.agent_network_requirement
                    WHERE agent_id = %(agent_id)s AND valid_to IS NULL
                    ORDER BY system_name, network_requirement_id
                    """,
                    {"agent_id": agent_id},
                ).fetchall()
            )
            return item

    def deployments(self, limit, offset, lifecycle_status=None, runtime_id=None, primary_agent_id=None):
        select_sql = """
            SELECT ad.agent_deployment_id AS stable_id, ad.agent_deployment_id,
                   a.name AS name,
                   ad.lifecycle_status::text AS lifecycle_status,
                   ad.valid_from, ad.valid_to, ad.row_version,
                   a.agent_id AS primary_agent_id, a.name AS primary_agent_name,
                   r.runtime_id, r.name AS runtime_name,
                   s.server_id, s.name AS server_name
            FROM asset.agent_deployment ad
            JOIN asset.agent_asset a ON a.agent_id = ad.primary_agent_id
            JOIN asset.runtime_instance r ON r.runtime_id = ad.runtime_id
            JOIN asset.server_asset s ON s.server_id = r.server_id
        """
        count_sql = """
            SELECT count(*) AS total
            FROM asset.agent_deployment ad
            JOIN asset.agent_asset a ON a.agent_id = ad.primary_agent_id
            JOIN asset.runtime_instance r ON r.runtime_id = ad.runtime_id
            JOIN asset.server_asset s ON s.server_id = r.server_id
        """
        clauses = []
        params = {}
        add_filter(clauses, params, lifecycle_status, "ad.lifecycle_status = %(lifecycle_status)s", "lifecycle_status")
        add_filter(clauses, params, runtime_id, "ad.runtime_id = %(runtime_id)s", "runtime_id")
        add_filter(clauses, params, primary_agent_id, "ad.primary_agent_id = %(primary_agent_id)s", "primary_agent_id")
        with self.database.connection() as connection:
            return paginated(connection, select_sql, count_sql, clauses, params, limit, offset)

    def groups(self, limit, offset, resource_type=None, lifecycle_status=None, q=None):
        select_sql = """
            SELECT mg.group_id AS stable_id, mg.group_id, mg.group_code, mg.name,
                   mg.resource_type::text AS resource_type,
                   mg.lifecycle_status::text AS lifecycle_status,
                   mg.purpose,
                   CASE mg.resource_type::text
                        WHEN 'server' THEN (SELECT count(*) FROM asset.server_group_member sgm
                                            WHERE sgm.group_id = mg.group_id AND sgm.valid_to IS NULL)
                        WHEN 'runtime' THEN (SELECT count(*) FROM asset.runtime_group_member rgm
                                             WHERE rgm.group_id = mg.group_id AND rgm.valid_to IS NULL)
                        ELSE (SELECT count(*) FROM asset.agent_group_member agm
                              WHERE agm.group_id = mg.group_id AND agm.valid_to IS NULL)
                   END AS current_members,
                   mg.row_version, mg.updated_at
            FROM asset.management_group mg
        """
        count_sql = "SELECT count(*) AS total FROM asset.management_group mg"
        clauses = []
        params = {}
        add_filter(clauses, params, resource_type, "mg.resource_type = %(resource_type)s", "resource_type")
        add_filter(clauses, params, lifecycle_status, "mg.lifecycle_status = %(lifecycle_status)s", "lifecycle_status")
        if q:
            clauses.append("(mg.name ILIKE %(q)s OR mg.group_code ILIKE %(q)s)")
            params["q"] = f"%{q}%"
        with self.database.connection() as connection:
            return paginated(connection, select_sql, count_sql, clauses, params, limit, offset)

    def group(self, group_id: UUID):
        with self.database.connection() as connection:
            item = connection.execute(
                """
                SELECT group_id, group_code, name,
                       resource_type::text AS resource_type,
                       lifecycle_status::text AS lifecycle_status,
                       purpose, row_version, created_at, updated_at
                FROM asset.management_group
                WHERE group_id = %(group_id)s
                """,
                {"group_id": group_id},
            ).fetchone()
            if item is None:
                return None
            if item["resource_type"] == "server":
                item["members"] = list(
                    connection.execute(
                        """
                        SELECT s.server_id AS member_id, s.server_id, s.name,
                               s.lifecycle_status::text AS lifecycle_status,
                               host(s.internal_ip) AS internal_ip,
                               host(s.public_ip) AS public_ip,
                               s.provider_instance_id,
                               (SELECT count(*) FROM asset.runtime_instance r
                                WHERE r.server_id = s.server_id) AS runtime_count,
                               sgm.valid_from
                        FROM asset.server_group_member sgm
                        JOIN asset.server_asset s ON s.server_id = sgm.server_id
                        WHERE sgm.group_id = %(group_id)s AND sgm.valid_to IS NULL
                        ORDER BY s.name, s.server_id
                        """,
                        {"group_id": group_id},
                    ).fetchall()
                )
            elif item["resource_type"] == "runtime":
                item["members"] = list(
                    connection.execute(
                        """
                        SELECT r.runtime_id AS member_id, r.runtime_id, r.name,
                               r.lifecycle_status::text AS lifecycle_status,
                               r.deployment_mode::text AS deployment_mode,
                               rf.family_code, s.server_id, s.name AS server_name,
                               host(s.internal_ip) AS server_internal_ip,
                               rgm.valid_from
                        FROM asset.runtime_group_member rgm
                        JOIN asset.runtime_instance r ON r.runtime_id = rgm.runtime_id
                        JOIN asset.runtime_family rf ON rf.runtime_family_id = r.runtime_family_id
                        JOIN asset.server_asset s ON s.server_id = r.server_id
                        WHERE rgm.group_id = %(group_id)s AND rgm.valid_to IS NULL
                        ORDER BY r.name, r.runtime_id
                        """,
                        {"group_id": group_id},
                    ).fetchall()
                )
            else:
                item["members"] = list(
                    connection.execute(
                        """
                        SELECT a.agent_id AS member_id, a.agent_id, a.name,
                               a.lifecycle_status::text AS lifecycle_status,
                               a.service_archetype::text AS service_archetype,
                               d.department_code AS owning_department_code,
                               d.name AS owning_department_name,
                               r.runtime_id AS active_runtime_id,
                               r.name AS active_runtime_name,
                               agm.valid_from
                        FROM asset.agent_group_member agm
                        JOIN asset.agent_asset a ON a.agent_id = agm.agent_id
                        LEFT JOIN org.department d ON d.department_id = a.owning_department_id
                        LEFT JOIN asset.agent_deployment ad
                               ON ad.primary_agent_id = a.agent_id AND ad.lifecycle_status = 'active'
                        LEFT JOIN asset.runtime_instance r ON r.runtime_id = ad.runtime_id
                        WHERE agm.group_id = %(group_id)s AND agm.valid_to IS NULL
                        ORDER BY a.name, a.agent_id
                        """,
                        {"group_id": group_id},
                    ).fetchall()
                )
            item["current_members"] = len(item["members"])
            return item

    def imports(self, limit, offset, status=None):
        select_sql = """
            SELECT ib.import_batch_id AS stable_id, ib.import_batch_id, ib.file_name,
                   ib.file_name AS name,
                   ib.template_version, ib.import_mode::text AS import_mode,
                   ib.status::text AS status, ib.actor_ref, ib.summary,
                   ib.created_at, ib.committed_at,
                   (SELECT count(*) FROM ingest.import_row ir WHERE ir.import_batch_id = ib.import_batch_id) AS rows,
                   (SELECT count(*) FROM ingest.import_issue ii
                    WHERE ii.import_batch_id = ib.import_batch_id AND ii.severity = 'error') AS errors,
                   (SELECT count(*) FROM ingest.import_issue ii
                    WHERE ii.import_batch_id = ib.import_batch_id AND ii.severity = 'warning') AS warnings
            FROM ingest.import_batch ib
        """
        count_sql = "SELECT count(*) AS total FROM ingest.import_batch ib"
        clauses = []
        params = {}
        add_filter(clauses, params, status, "ib.status = %(status)s", "status")
        with self.database.connection() as connection:
            where = " AND ".join(clauses) if clauses else "TRUE"
            query_params = dict(params, limit=limit, offset=offset)
            rows = list(connection.execute(
                f"{select_sql} WHERE {where} "
                "ORDER BY ib.created_at DESC, ib.import_batch_id DESC "
                "LIMIT %(limit)s OFFSET %(offset)s",
                query_params,
            ).fetchall())
            total = connection.execute(f"{count_sql} WHERE {where}", params).fetchone()["total"]
            return {"data": rows, "page": {"limit": limit, "offset": offset, "total": total}}

    def import_batch(self, batch_id: UUID):
        with self.database.connection() as connection:
            item = connection.execute(
                """
                SELECT import_batch_id, file_name, file_sha256, template_version,
                       import_mode::text AS import_mode, status::text AS status,
                       actor_ref, summary, created_at, updated_at, committed_at
                FROM ingest.import_batch
                WHERE import_batch_id = %(batch_id)s
                """,
                {"batch_id": batch_id},
            ).fetchone()
            if item is None:
                return None
            item["rows"] = list(
                connection.execute(
                    """
                    SELECT import_row_id, sheet_name, row_number, target_object_type,
                           target_object_id, expected_row_version,
                           raw_payload, normalized_payload, status::text AS status
                    FROM ingest.import_row
                    WHERE import_batch_id = %(batch_id)s
                    ORDER BY sheet_name, row_number
                    """,
                    {"batch_id": batch_id},
                ).fetchall()
            )
            item["issues"] = list(
                connection.execute(
                    """
                    SELECT import_issue_id, import_row_id, severity, issue_code,
                           field_path, message, created_at
                    FROM ingest.import_issue
                    WHERE import_batch_id = %(batch_id)s
                    ORDER BY severity DESC, created_at, import_issue_id
                    """,
                    {"batch_id": batch_id},
                ).fetchall()
            )
            item["changes"] = list(
                connection.execute(
                    """
                    SELECT ic.import_change_id, ic.import_row_id, ic.field_path,
                           ic.action::text AS action, ic.before_value, ic.after_value, ic.selected
                    FROM ingest.import_change ic
                    JOIN ingest.import_row ir ON ir.import_row_id = ic.import_row_id
                    WHERE ir.import_batch_id = %(batch_id)s
                    ORDER BY ir.sheet_name, ir.row_number, ic.field_path
                    """,
                    {"batch_id": batch_id},
                ).fetchall()
            )
            return item

    def data_quality_issues(self, limit, offset, severity=None, issue_code=None, batch_id=None):
        select_sql = """
            SELECT ii.import_issue_id AS stable_id, ii.import_issue_id,
                   ii.issue_code AS name,
                   ii.severity, ii.issue_code, ii.field_path, ii.message, ii.created_at,
                   ib.import_batch_id, ib.file_name, ib.status::text AS batch_status,
                   ir.sheet_name, ir.row_number,
                   ir.target_object_type, ir.target_object_id,
                   ir.expected_row_version,
                   ir.raw_payload, ir.normalized_payload,
                   ir.status::text AS row_status
            FROM ingest.import_issue ii
            JOIN ingest.import_batch ib ON ib.import_batch_id = ii.import_batch_id
            LEFT JOIN ingest.import_row ir ON ir.import_row_id = ii.import_row_id
        """
        count_sql = """
            SELECT count(*) AS total
            FROM ingest.import_issue ii
            JOIN ingest.import_batch ib ON ib.import_batch_id = ii.import_batch_id
            LEFT JOIN ingest.import_row ir ON ir.import_row_id = ii.import_row_id
        """
        clauses = []
        params = {}
        add_filter(clauses, params, severity, "ii.severity = %(severity)s", "severity")
        add_filter(clauses, params, issue_code, "ii.issue_code = %(issue_code)s", "issue_code")
        add_filter(clauses, params, batch_id, "ii.import_batch_id = %(batch_id)s", "batch_id")
        with self.database.connection() as connection:
            return paginated(connection, select_sql, count_sql, clauses, params, limit, offset)
