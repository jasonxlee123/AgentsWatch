import base64
import json
import os
import subprocess
import threading
import time
import tempfile
import unittest
from io import BytesIO
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import psycopg
from openpyxl import load_workbook

from agentswatch_api.database import WriteDatabase
from agentswatch_api.import_management import BulkImportService
from agentswatch_api.organization_management import OrganizationManagementService


BASE_URL = os.environ.get("AGENTSWATCH_CORE_API_TEST_URL")
DATABASE_URL = os.environ.get("AGENTSWATCH_CORE_API_TEST_DATABASE_URL")


@unittest.skipUnless(BASE_URL, "run through scripts/test_core_api_local.sh")
class CoreApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cookie_jar = CookieJar()
        cls.opener = build_opener(HTTPCookieProcessor(cls.cookie_jar))
        cls._login(cls.opener, cls.cookie_jar, "integration-admin", "AgentsWatch!Test2026")

    @classmethod
    def _login(cls, opener, cookie_jar, username, password):
        challenge_request = Request(
            f"{BASE_URL}/api/v1/auth/challenge", data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        with opener.open(challenge_request, timeout=5) as response:
            challenge = json.load(response)["data"]
        with psycopg.connect(DATABASE_URL) as connection:
            offset = connection.execute(
                "SELECT expected_offset FROM security.login_challenge WHERE challenge_id = %s",
                (challenge["challenge_id"],),
            ).fetchone()[0]
        login_request = Request(
            f"{BASE_URL}/api/v1/auth/login",
            data=json.dumps({
                "username": username,
                "password": password,
                "challenge_id": challenge["challenge_id"],
                "challenge_offset": offset,
            }).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with opener.open(login_request, timeout=5) as response:
            body = json.load(response)
        return body["data"]["user"]

    @classmethod
    def csrf_token(cls, cookie_jar=None):
        jar = cookie_jar or cls.cookie_jar
        return next(cookie.value for cookie in jar if cookie.name == "agentswatch_csrf")

    def get(self, path):
        with self.opener.open(f"{BASE_URL}{path}", timeout=5) as response:
            return response.status, json.loads(response.read())

    @staticmethod
    def error_json(error):
        return json.loads(error.read())

    @staticmethod
    def _json_request(opener, method, path, payload, headers, timeout=15):
        body = None if payload is None else json.dumps(payload).encode()
        request = Request(f"{BASE_URL}{path}", data=body, method=method)
        for name, value in headers.items():
            request.add_header(name, value)
        with opener.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())

    def request(
        self, method, path, payload=None, *, auto_confirm=True, headers=None,
        opener=None, cookie_jar=None,
    ):
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        if method != "GET":
            request_headers.setdefault(
                "X-CSRF-Token", self.csrf_token(cookie_jar),
            )
        selected_opener = opener or self.opener
        try:
            return self._json_request(
                selected_opener, method, path, payload, request_headers,
            )
        except HTTPError as error:
            if not auto_confirm or error.code != 409:
                raise
            error_body = self.error_json(error)
            detail = error_body.get("detail")
            if not isinstance(detail, dict) or detail.get("code") != "CONFIRMATION_REQUIRED":
                raise
            confirmation_id = detail.get("confirmation_id")
            confirmation_header = detail.get("confirmation_header", "X-Confirmation-ID")
            if not confirmation_id:
                raise AssertionError("confirmation challenge omitted confirmation_id")
            confirmed_headers = {
                **request_headers,
                confirmation_header: confirmation_id,
            }
            return self._json_request(
                selected_opener, method, path, payload, confirmed_headers,
            )

    def bearer_request(self, api_key, method, path, payload=None, *, headers=None):
        return self._json_request(
            build_opener(),
            method,
            path,
            payload,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                **(headers or {}),
            },
        )

    def request_file(self, path, file_name, content):
        boundary = f"----AgentsWatch{int(time.time() * 1000000)}"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        request = Request(f"{BASE_URL}{path}", data=body, method="POST")
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        request.add_header("X-CSRF-Token", self.csrf_token())
        with self.opener.open(request, timeout=20) as response:
            return response.status, json.loads(response.read())

    @staticmethod
    def import_workbook(suffix):
        content = BulkImportService.template_bytes()
        workbook = load_workbook(BytesIO(content))
        workbook["部门"].delete_rows(2, workbook["部门"].max_row)
        workbook["人员"].delete_rows(2, workbook["人员"].max_row)
        workbook["服务器"].delete_rows(2, workbook["服务器"].max_row)
        workbook["运行时"].delete_rows(2, workbook["运行时"].max_row)
        workbook["智能体"].delete_rows(2, workbook["智能体"].max_row)
        department_code = f"DEP-IMPORT-{suffix}"
        employee_no = f"EMP-IMPORT-{suffix}"
        server_code = f"SRV-IMPORT-{suffix}"
        runtime_code = f"RT-IMPORT-{suffix}"
        agent_code = f"AGT-IMPORT-{suffix}"
        workbook["部门"].append([department_code, None, "批量导入部门", True])
        workbook["人员"].append([employee_no, "批量导入人员", department_code, "active"])
        workbook["服务器"].append([server_code, "prod", None, "批量导入服务器", "planned", 4, 8, 100, None, None, "Linux", None, "x86_64", "openclaw"])
        workbook["运行时"].append([runtime_code, server_code, "openclaw", "批量导入运行时", "gateway", "baremetal", None, "planned", "127.0.0.1", 18789, "openclaw", "/etc/openclaw", "/var/lib/openclaw", None, None])
        workbook["智能体"].append([agent_code, "批量导入智能体", "primary", None, "department", "requested", department_code, None, None, None, department_code, None, None, "集成测试"])
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        return output.getvalue(), {
            "department": department_code, "person": employee_no, "server": server_code,
            "runtime": runtime_code, "agent": agent_code,
        }

    def test_management_group_crud_runtime_filter_and_member_replacement(self):
        _, runtime_response = self.get("/api/v1/runtimes?limit=1")
        runtime = runtime_response["data"][0]
        suffix = str(int(time.time() * 1000000))
        group_code = f"runtime-integration-{suffix}"
        status_code, created = self.request("POST", "/api/v1/groups", {
            "group_code": group_code,
            "name": "运行时集成测试组",
            "resource_type": "runtime",
            "purpose": "验证分组维护和筛选闭环",
            "member_ids": [runtime["runtime_id"]],
        })
        self.assertEqual(status_code, 201)
        group = created["data"]
        self.assertEqual(group["resource_type"], "runtime")
        self.assertEqual([item["runtime_id"] for item in group["members"]], [runtime["runtime_id"]])

        _, filtered = self.get(f"/api/v1/runtimes?group_code={group_code}&limit=100")
        self.assertEqual([item["runtime_id"] for item in filtered["data"]], [runtime["runtime_id"]])

        status_code, updated = self.request("PATCH", f"/api/v1/groups/{group['group_id']}", {
            "row_version": group["row_version"],
            "name": "运行时集成测试组（已更新）",
            "member_ids": [],
        })
        self.assertEqual(status_code, 200)
        group = updated["data"]
        self.assertEqual(group["current_members"], 0)

        status_code, retired = self.request(
            "DELETE", f"/api/v1/groups/{group['group_id']}?row_version={group['row_version']}",
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(retired["data"]["lifecycle_status"], "retired")

    def test_zzzz_five_entity_excel_import_is_previewed_and_committed_atomically(self):
        suffix = str(int(time.time() * 1000000))
        workbook, codes = self.import_workbook(suffix)
        status_code, preview = self.request_file(
            "/api/v1/imports/preview", f"bulk-{suffix}.xlsx", workbook,
        )
        self.assertEqual(status_code, 201)
        batch = preview["data"]
        self.assertEqual(batch["status"], "ready")
        self.assertEqual(batch["summary"]["rows"], 5)
        self.assertEqual(batch["summary"]["errors"], 0)
        self.assertEqual(
            {name: values["new"] for name, values in batch["summary"]["entities"].items()},
            {"部门": 1, "人员": 1, "服务器": 1, "运行时": 1, "智能体": 1},
        )

        status_code, committed = self.request(
            "POST", f"/api/v1/imports/{batch['import_batch_id']}/commit", {},
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(committed["data"]["status"], "committed")
        with psycopg.connect(DATABASE_URL) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM org.department WHERE department_code=%s", (codes["department"],),
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM org.person WHERE employee_no=%s", (codes["person"],),
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM asset.external_identifier WHERE source_system='bulk_import' AND identifier_value=ANY(%s)",
                ([codes["server"], codes["runtime"], codes["agent"]],),
            ).fetchone()[0], 3)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM audit.change_log WHERE import_batch_id=%s",
                (batch["import_batch_id"],),
            ).fetchone()[0], 5)

        _, repeated = self.request_file(
            "/api/v1/imports/preview", f"bulk-repeat-{suffix}.xlsx", workbook,
        )
        self.assertEqual(repeated["data"]["status"], "ready")
        self.assertTrue(all(row["status"] == "unchanged" for row in repeated["data"]["rows"]))

        update_workbook = load_workbook(BytesIO(workbook))
        update_workbook["部门"]["C2"] = "批量导入部门（已更新）"
        update_output = BytesIO()
        update_workbook.save(update_output)
        update_workbook.close()
        _, update_preview = self.request_file(
            "/api/v1/imports/preview", f"bulk-update-{suffix}.xlsx", update_output.getvalue(),
        )
        self.assertEqual(update_preview["data"]["summary"]["entities"]["部门"]["changed"], 1)
        _, update_committed = self.request(
            "POST", f"/api/v1/imports/{update_preview['data']['import_batch_id']}/commit", {},
        )
        self.assertEqual(update_committed["data"]["status"], "committed")
        with psycopg.connect(DATABASE_URL) as connection:
            self.assertEqual(connection.execute(
                "SELECT name FROM org.department WHERE department_code=%s",
                (codes["department"],),
            ).fetchone()[0], "批量导入部门（已更新）")
            self.assertEqual(connection.execute(
                "SELECT changed_fields FROM audit.change_log WHERE import_batch_id=%s",
                (update_preview["data"]["import_batch_id"],),
            ).fetchone()[0], ["name"])

        conflict_content = BulkImportService.template_bytes()
        conflict_workbook = load_workbook(BytesIO(conflict_content))
        for sheet_name in ("部门", "人员", "服务器", "运行时", "智能体"):
            conflict_workbook[sheet_name].delete_rows(2, conflict_workbook[sheet_name].max_row)
        rollback_department = f"DEP-ROLLBACK-{suffix}"
        conflict_workbook["部门"].append([rollback_department, None, "应回滚部门", True])
        conflict_workbook["人员"].append(["E003", "批量更新应回滚", rollback_department, "active"])
        output = BytesIO()
        conflict_workbook.save(output)
        conflict_workbook.close()
        _, conflict_preview = self.request_file(
            "/api/v1/imports/preview", f"bulk-conflict-{suffix}.xlsx", output.getvalue(),
        )
        self.assertEqual(conflict_preview["data"]["status"], "ready")
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "UPDATE org.person SET display_name='集成测试并发修改' WHERE employee_no='E003'"
            )
        with self.assertRaises(HTTPError) as conflict_response:
            self.request(
                "POST",
                f"/api/v1/imports/{conflict_preview['data']['import_batch_id']}/commit",
                {},
            )
        self.assertEqual(conflict_response.exception.code, 409)
        with psycopg.connect(DATABASE_URL) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM org.department WHERE department_code=%s",
                (rollback_department,),
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT display_name FROM org.person WHERE employee_no='E003'"
            ).fetchone()[0], "集成测试并发修改")

    def test_authentication_and_role_enforcement(self):
        _, me = self.get("/api/v1/auth/me")
        self.assertEqual(me["data"]["username"], "integration-admin")
        self.assertIn("manage_users", me["data"]["permissions"])

        username = f"viewer-{int(time.time() * 1000)}"
        _, created = self.request("POST", "/api/v1/users", {
            "username": username,
            "display_name": "权限测试只读用户",
            "password": "Viewer!Secure2026",
            "role": "viewer",
        })
        self.assertEqual(created["data"]["role"], "viewer")
        viewer_jar = CookieJar()
        viewer_opener = build_opener(HTTPCookieProcessor(viewer_jar))
        viewer = self._login(viewer_opener, viewer_jar, username, "Viewer!Secure2026")
        self.assertEqual(viewer["permissions"], ["view"])
        forbidden = Request(
            f"{BASE_URL}/api/v1/servers",
            data=json.dumps({
                "environment_id": "00000000-0000-4000-8000-000000000101",
                "name": "只读用户不应创建此服务器",
            }).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "X-CSRF-Token": self.csrf_token(viewer_jar)},
        )
        with self.assertRaises(HTTPError) as context:
            viewer_opener.open(forbidden, timeout=5)
        self.assertEqual(context.exception.code, 403)
        with viewer_opener.open(f"{BASE_URL}/api/v1/overview", timeout=5) as response:
            self.assertEqual(response.status, 200)
        failed_login_opener = build_opener(HTTPCookieProcessor(CookieJar()))
        for _ in range(5):
            with self.assertRaises(HTTPError) as failed_login:
                self._login(
                    failed_login_opener, CookieJar(), username, "Wrong!Secure2026"
                )
            self.assertEqual(failed_login.exception.code, 401)
        with psycopg.connect(DATABASE_URL) as connection:
            attempts, locked_until = connection.execute(
                "SELECT failed_login_attempts, locked_until FROM security.user_account WHERE username = %s",
                (username,),
            ).fetchone()
        self.assertEqual(attempts, 5)
        self.assertIsNotNone(locked_until)

        operator_username = f"operator-{int(time.time() * 1000)}"
        self.request("POST", "/api/v1/users", {
            "username": operator_username,
            "display_name": "权限测试操作员",
            "password": "Operator!Secure2026",
            "role": "operator",
        })
        operator_jar = CookieJar()
        operator_opener = build_opener(HTTPCookieProcessor(operator_jar))
        operator = self._login(
            operator_opener, operator_jar, operator_username, "Operator!Secure2026"
        )
        self.assertIn("manage_assets", operator["permissions"])
        self.assertNotIn("manage_users", operator["permissions"])
        with self.assertRaises(HTTPError) as operator_forbidden:
            operator_opener.open(f"{BASE_URL}/api/v1/users", timeout=5)
        self.assertEqual(operator_forbidden.exception.code, 403)

        with self.assertRaises(HTTPError) as weak_password:
            self.request("POST", "/api/v1/users", {
                "username": f"weak-{int(time.time() * 1000)}",
                "display_name": "弱密码账号",
                "password": "weak-password",
                "role": "viewer",
            })
        self.assertEqual(weak_password.exception.code, 422)

    def test_zz_api_key_lifecycle_and_bearer_role_permissions(self):
        suffix = str(int(time.time() * 1000000))
        operator_username = f"key-operator-{suffix}"
        viewer_username = f"key-viewer-{suffix}"
        for username, role, password in (
            (operator_username, "operator", "Operator!ApiKey2026"),
            (viewer_username, "viewer", "Viewer!ApiKey2026"),
        ):
            status, created = self.request("POST", "/api/v1/users", {
                "username": username,
                "display_name": f"API Key {role} 验证账号",
                "password": password,
                "role": role,
            })
            self.assertEqual(status, 201)
            self.assertEqual(created["data"]["role"], role)

        operator_jar = CookieJar()
        operator_opener = build_opener(HTTPCookieProcessor(operator_jar))
        self._login(
            operator_opener, operator_jar,
            operator_username, "Operator!ApiKey2026",
        )
        _, initial = self.request(
            "GET", "/api/v1/auth/api-key",
            opener=operator_opener, cookie_jar=operator_jar,
        )
        self.assertFalse(initial["data"]["active"])

        create_status, created = self.request(
            "POST", "/api/v1/auth/api-key", {},
            opener=operator_opener, cookie_jar=operator_jar,
        )
        self.assertEqual(create_status, 201)
        first_key = created["data"]["api_key"]
        first_prefix = created["data"]["prefix"]
        self.assertRegex(first_key, r"^awk_[0-9a-f]{16}_[A-Za-z0-9_-]{43}$")
        self.assertTrue(created["data"]["active"])

        _, status_body = self.request(
            "GET", "/api/v1/auth/api-key",
            opener=operator_opener, cookie_jar=operator_jar,
        )
        self.assertNotIn("api_key", status_body["data"])
        self.assertEqual(status_body["data"]["prefix"], first_prefix)
        with psycopg.connect(DATABASE_URL) as connection:
            key_prefix, key_hash = connection.execute(
                """
                SELECT key_prefix, key_hash
                FROM security.api_key key_record
                JOIN security.user_account account USING (user_id)
                WHERE account.username = %s AND key_record.revoked_at IS NULL
                """,
                (operator_username,),
            ).fetchone()
        self.assertEqual(key_prefix, first_prefix)
        self.assertEqual(len(key_hash.strip()), 64)
        self.assertNotEqual(key_hash.strip(), first_key)

        me_status, me = self.bearer_request(
            first_key, "GET", "/api/v1/auth/me",
        )
        self.assertEqual(me_status, 200)
        self.assertEqual(me["data"]["username"], operator_username)
        self.assertEqual(me["data"]["role"], "operator")

        group_code = f"bearer-group-{suffix}"
        group_status, group_body = self.bearer_request(
            first_key, "POST", "/api/v1/groups", {
                "group_code": group_code,
                "name": "Bearer 操作员创建的测试组",
                "resource_type": "server",
                "member_ids": [],
            },
        )
        self.assertEqual(group_status, 201)
        group = group_body["data"]

        viewer_jar = CookieJar()
        viewer_opener = build_opener(HTTPCookieProcessor(viewer_jar))
        self._login(
            viewer_opener, viewer_jar, viewer_username, "Viewer!ApiKey2026",
        )
        _, viewer_created = self.request(
            "POST", "/api/v1/auth/api-key", {},
            opener=viewer_opener, cookie_jar=viewer_jar,
        )
        viewer_key = viewer_created["data"]["api_key"]
        overview_status, _ = self.bearer_request(
            viewer_key, "GET", "/api/v1/overview",
        )
        self.assertEqual(overview_status, 200)
        with self.assertRaises(HTTPError) as viewer_forbidden:
            self.bearer_request(viewer_key, "POST", "/api/v1/servers", {
                "environment_id": "00000000-0000-4000-8000-000000000101",
                "name": "只读 Bearer 不应创建的服务器",
            })
        self.assertEqual(viewer_forbidden.exception.code, 403)

        _, rotated = self.request(
            "POST", "/api/v1/auth/api-key/rotate", {},
            opener=operator_opener, cookie_jar=operator_jar,
        )
        second_key = rotated["data"]["api_key"]
        self.assertNotEqual(second_key, first_key)
        self.assertNotEqual(rotated["data"]["prefix"], first_prefix)
        with self.assertRaises(HTTPError) as old_key_rejected:
            self.bearer_request(first_key, "GET", "/api/v1/auth/me")
        self.assertEqual(old_key_rejected.exception.code, 401)
        _, rotated_me = self.bearer_request(second_key, "GET", "/api/v1/auth/me")
        self.assertEqual(rotated_me["data"]["username"], operator_username)

        _, revoked = self.request(
            "DELETE", "/api/v1/auth/api-key",
            opener=operator_opener, cookie_jar=operator_jar,
        )
        self.assertFalse(revoked["data"]["active"])
        with self.assertRaises(HTTPError) as revoked_key_rejected:
            self.bearer_request(second_key, "GET", "/api/v1/auth/me")
        self.assertEqual(revoked_key_rejected.exception.code, 401)
        with psycopg.connect(DATABASE_URL) as connection:
            active_count, total_count = connection.execute(
                """
                SELECT count(*) FILTER (WHERE key_record.revoked_at IS NULL), count(*)
                FROM security.api_key key_record
                JOIN security.user_account account USING (user_id)
                WHERE account.username = %s
                """,
                (operator_username,),
            ).fetchone()
        self.assertEqual(active_count, 0)
        self.assertEqual(total_count, 2)

        self.request(
            "DELETE", "/api/v1/auth/api-key",
            opener=viewer_opener, cookie_jar=viewer_jar,
        )
        self.request(
            "DELETE",
            f"/api/v1/groups/{group['group_id']}?row_version={group['row_version']}",
        )

    def test_zz_confirmation_is_bound_to_user_path_query_and_is_one_time(self):
        suffix = str(int(time.time() * 1000000))
        groups = []
        for marker in ("a", "b"):
            _, response = self.request("POST", "/api/v1/groups", {
                "group_code": f"confirmation-{marker}-{suffix}",
                "name": f"确认绑定验证组 {marker.upper()}",
                "resource_type": "server",
                "member_ids": [],
            })
            groups.append(response["data"])
        first, second = groups
        first_path = (
            f"/api/v1/groups/{first['group_id']}?row_version={first['row_version']}"
        )
        second_path = (
            f"/api/v1/groups/{second['group_id']}?row_version={second['row_version']}"
        )

        with self.assertRaises(HTTPError) as challenge_response:
            self.request("DELETE", first_path, auto_confirm=False)
        self.assertEqual(challenge_response.exception.code, 409)
        challenge = self.error_json(challenge_response.exception)["detail"]
        self.assertEqual(challenge["code"], "CONFIRMATION_REQUIRED")
        self.assertEqual(challenge["confirmation_header"], "X-Confirmation-ID")
        self.assertRegex(challenge["confirmation_id"], r"^cfm_[0-9a-f]{32}$")
        confirmation_headers = {
            challenge["confirmation_header"]: challenge["confirmation_id"],
        }

        with self.assertRaises(HTTPError) as path_mismatch:
            self.request(
                "DELETE", second_path, headers=confirmation_headers,
                auto_confirm=False,
            )
        self.assertEqual(path_mismatch.exception.code, 409)
        self.assertEqual(
            self.error_json(path_mismatch.exception)["detail"]["code"],
            "CONFIRMATION_INVALID",
        )
        with self.assertRaises(HTTPError) as query_mismatch:
            self.request(
                "DELETE",
                f"/api/v1/groups/{first['group_id']}?row_version={first['row_version'] + 1}",
                headers=confirmation_headers,
                auto_confirm=False,
            )
        self.assertEqual(query_mismatch.exception.code, 409)
        self.assertEqual(
            self.error_json(query_mismatch.exception)["detail"]["code"],
            "CONFIRMATION_INVALID",
        )

        operator_username = f"confirmation-operator-{suffix}"
        self.request("POST", "/api/v1/users", {
            "username": operator_username,
            "display_name": "跨用户确认验证操作员",
            "password": "Operator!Confirm2026",
            "role": "operator",
        })
        operator_jar = CookieJar()
        operator_opener = build_opener(HTTPCookieProcessor(operator_jar))
        self._login(
            operator_opener, operator_jar,
            operator_username, "Operator!Confirm2026",
        )
        with self.assertRaises(HTTPError) as user_mismatch:
            self.request(
                "DELETE", first_path, headers=confirmation_headers,
                auto_confirm=False,
                opener=operator_opener, cookie_jar=operator_jar,
            )
        self.assertEqual(user_mismatch.exception.code, 409)
        self.assertEqual(
            self.error_json(user_mismatch.exception)["detail"]["code"],
            "CONFIRMATION_INVALID",
        )

        status, retired = self.request(
            "DELETE", first_path, headers=confirmation_headers,
            auto_confirm=False,
        )
        self.assertEqual(status, 200)
        self.assertEqual(retired["data"]["lifecycle_status"], "retired")
        with self.assertRaises(HTTPError) as replayed:
            self.request(
                "DELETE", first_path, headers=confirmation_headers,
                auto_confirm=False,
            )
        self.assertEqual(replayed.exception.code, 409)
        self.assertEqual(
            self.error_json(replayed.exception)["detail"]["code"],
            "CONFIRMATION_INVALID",
        )

        confirmation_uuid = challenge["confirmation_id"].removeprefix("cfm_")
        with psycopg.connect(DATABASE_URL) as connection:
            used_at, request_path, query_hash, body_hash = connection.execute(
                """
                SELECT used_at, request_path, query_hash, body_hash
                FROM security.operation_confirmation
                WHERE confirmation_id = %s
                """,
                (confirmation_uuid,),
            ).fetchone()
        self.assertIsNotNone(used_at)
        self.assertEqual(request_path, f"/api/v1/groups/{first['group_id']}")
        self.assertEqual(len(query_hash.strip()), 64)
        self.assertEqual(len(body_hash.strip()), 64)

        self.request("DELETE", second_path)

    def test_zz_medium_risk_retry_confirmation_is_bound_to_request_body(self):
        suffix = str(int(time.time() * 1000000))
        _, runtime_response = self.request("POST", "/api/v1/runtimes", {
            "server_id": "00000000-0000-4000-8000-000000000501",
            "runtime_family_id": "00000000-0000-4000-8000-000000000601",
            "name": f"确认重试验证运行时 {suffix}",
            "deployment_mode": "baremetal",
            "lifecycle_status": "active",
            "runtime_account": "openclaw",
            "management_driver": "mock",
            "management_target": f"confirmation-retry-{suffix}",
        })
        runtime = runtime_response["data"]
        with psycopg.connect(DATABASE_URL) as connection:
            original_run_id = connection.execute(
                """
                INSERT INTO automation.operation_run (
                    operation_code, status, parameters, requested_by, target_count,
                    failed_count, playbook_version, target_type, started_at, finished_at
                ) VALUES (
                    'runtime.stop', 'failed', '{}'::jsonb, 'integration-fixture', 1,
                    1, '1.0.0+integration-fixture', 'runtime', now(), now()
                ) RETURNING run_id
                """
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO automation.operation_host_result (
                    run_id, server_id, runtime_id, target_key, status,
                    error_message, started_at, finished_at
                ) VALUES (%s, %s, %s, %s, 'failed', 'integration fixture', now(), now())
                """,
                (
                    original_run_id,
                    "00000000-0000-4000-8000-000000000501",
                    runtime["runtime_id"],
                    runtime["runtime_id"],
                ),
            )

        retry_path = f"/api/v1/automation/runs/{original_run_id}/retry"
        with self.assertRaises(HTTPError) as challenge_response:
            self.request("POST", retry_path, {}, auto_confirm=False)
        challenge = self.error_json(challenge_response.exception)["detail"]
        self.assertEqual(challenge["code"], "CONFIRMATION_REQUIRED")
        confirmation_headers = {
            challenge["confirmation_header"]: challenge["confirmation_id"],
        }

        with self.assertRaises(HTTPError) as body_mismatch:
            self.request(
                "POST", retry_path, {"requested_by": "changed-request"},
                headers=confirmation_headers, auto_confirm=False,
            )
        self.assertEqual(body_mismatch.exception.code, 409)
        self.assertEqual(
            self.error_json(body_mismatch.exception)["detail"]["code"],
            "CONFIRMATION_INVALID",
        )

        retry_status, retried = self.request(
            "POST", retry_path, {}, headers=confirmation_headers,
            auto_confirm=False,
        )
        self.assertEqual(retry_status, 202)
        final = self.wait_run(retried["data"]["run_id"])
        self.assertEqual(final["retry_of_run_id"], str(original_run_id))

        _, latest_runtime = self.get(f"/api/v1/runtimes/{runtime['runtime_id']}")
        self.request(
            "DELETE",
            f"/api/v1/runtimes/{runtime['runtime_id']}?row_version={latest_runtime['data']['row_version']}",
        )

    def test_slider_challenge_is_consumed_after_one_attempt(self):
        cookie_jar = CookieJar()
        opener = build_opener(HTTPCookieProcessor(cookie_jar))
        challenge_request = Request(
            f"{BASE_URL}/api/v1/auth/challenge", data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        with opener.open(challenge_request, timeout=5) as response:
            challenge = json.load(response)["data"]
        with psycopg.connect(DATABASE_URL) as connection:
            offset = connection.execute(
                "SELECT expected_offset FROM security.login_challenge WHERE challenge_id = %s",
                (challenge["challenge_id"],),
            ).fetchone()[0]

        def login_request(challenge_offset):
            return Request(
                f"{BASE_URL}/api/v1/auth/login",
                data=json.dumps({
                    "username": "integration-admin",
                    "password": "AgentsWatch!Test2026",
                    "challenge_id": challenge["challenge_id"],
                    "challenge_offset": challenge_offset,
                }).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )

        wrong_offset = 0 if offset > 5 else 238
        with self.assertRaises(HTTPError) as wrong:
            opener.open(login_request(wrong_offset), timeout=5)
        self.assertEqual(wrong.exception.code, 401)
        with self.assertRaises(HTTPError) as replayed:
            opener.open(login_request(offset), timeout=5)
        self.assertEqual(replayed.exception.code, 401)

    def test_user_can_change_password_and_current_session_is_revoked(self):
        username = f"password-user-{int(time.time() * 1000)}"
        old_password = "Original!Secure2026"
        new_password = "Changed!Secure2027"
        self.request("POST", "/api/v1/users", {
            "username": username,
            "display_name": "改密测试用户",
            "password": old_password,
            "role": "viewer",
        })
        cookie_jar = CookieJar()
        opener = build_opener(HTTPCookieProcessor(cookie_jar))
        self._login(opener, cookie_jar, username, old_password)
        change_request = Request(
            f"{BASE_URL}/api/v1/auth/change-password",
            data=json.dumps({
                "old_password": old_password,
                "new_password": new_password,
            }).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": self.csrf_token(cookie_jar),
            },
        )
        with opener.open(change_request, timeout=5) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(HTTPError) as revoked:
            opener.open(f"{BASE_URL}/api/v1/auth/me", timeout=5)
        self.assertEqual(revoked.exception.code, 401)

        with self.assertRaises(HTTPError) as old_rejected:
            self._login(opener, cookie_jar, username, old_password)
        self.assertEqual(old_rejected.exception.code, 401)
        relogged = self._login(opener, cookie_jar, username, new_password)
        self.assertEqual(relogged["username"], username)

    def wait_run(self, run_id):
        final = None
        for _ in range(1200):
            _, response = self.get(f"/api/v1/automation/runs/{run_id}")
            final = response["data"]
            if final["status"] not in {"queued", "running"}:
                return final
            time.sleep(0.1)
        self.fail(f"operation run did not finish: {run_id}")

    def test_health_confirms_read_only_database_transaction(self):
        status, body = self.get("/healthz")

        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database_user"], "agentswatch_reader")
        self.assertEqual(body["transaction_read_only"], "on")

    def test_overview_returns_core_asset_counts(self):
        _, body = self.get("/api/v1/overview")
        counts = body["data"]["counts"]

        self.assertEqual(counts["servers"], 3)
        self.assertEqual(counts["runtimes"], 3)
        self.assertEqual(counts["primary_agents"], 4)
        self.assertEqual(counts["subagents"], 3)
        self.assertEqual(counts["active_deployments"], 2)

    def test_reference_data_and_people_support_management_forms(self):
        _, reference = self.get("/api/v1/reference-data")
        _, people = self.get("/api/v1/people?department_code=DEP-LEGAL")

        self.assertEqual(len(reference["data"]["environments"]), 1)
        self.assertEqual(len(reference["data"]["cloud_providers"]), 1)
        self.assertEqual(len(reference["data"]["runtime_families"]), 2)
        self.assertEqual(len(reference["data"]["departments"]), 3)
        self.assertEqual(people["page"]["total"], 2)

    def test_server_list_supports_filter_search_and_pagination(self):
        _, active = self.get("/api/v1/servers?lifecycle_status=active&limit=1&offset=0")
        _, searched = self.get("/api/v1/servers?q=10.0.0.12")

        self.assertEqual(active["page"], {"limit": 1, "offset": 0, "total": 2})
        self.assertEqual(len(active["data"]), 1)
        self.assertEqual(searched["page"]["total"], 1)
        self.assertEqual(searched["data"][0]["provider_instance_id"], "ECS-002")

    def test_server_detail_contains_runtime_and_group_relationships(self):
        _, body = self.get("/api/v1/servers/00000000-0000-4000-8000-000000000502")
        item = body["data"]

        self.assertEqual(item["environment_code"], "prod")
        self.assertEqual(item["provider_code"], "huawei_cloud")
        self.assertGreaterEqual(len(item["runtimes"]), 2)
        self.assertEqual(len(item["groups"]), 2)

    def test_server_management_crud_connection_and_operation(self):
        _, catalog = self.get("/api/v1/automation/operations")
        server_tasks = [item for item in catalog["data"]["operations"] if item["scope"] == "server"]
        self.assertTrue({
            "server.bootstrap_ssh", "server.check_connection", "server.collect_facts", "server.discover_runtimes",
        }.issubset({item["code"] for item in server_tasks}))
        self.assertFalse(next(item for item in server_tasks if item["code"] == "server.bootstrap_ssh")["supports_batch"])
        self.assertTrue(all(
            item["supports_batch"] for item in server_tasks if item["code"] != "server.bootstrap_ssh"
        ))
        self.assertTrue(all(item["description"] and item["category"] and item["version"] for item in server_tasks))

        create_status, created = self.request("POST", "/api/v1/servers", {
            "environment_id": "00000000-0000-4000-8000-000000000101",
            "cloud_provider_id": "00000000-0000-4000-8000-000000000401",
            "name": "本机自动化验证服务器",
            "provider_instance_id": "LOCAL-AUTOMATION-001",
            "lifecycle_status": "active",
            "internal_ip": "127.0.0.2",
            "cpu_cores": 4,
            "memory_gib": 8,
            "disk_gib": 100,
            "group_ids": ["00000000-0000-4000-8000-000000000801"],
        })
        self.assertEqual(create_status, 201)
        server = created["data"]
        server_id = server["server_id"]
        self.assertEqual(server["groups"][0]["group_code"], "server-prod")

        _, updated = self.request("PATCH", f"/api/v1/servers/{server_id}", {
            "row_version": server["row_version"],
            "name": "本机自动化验证节点",
            "os_name": "macOS",
        })
        server = updated["data"]
        self.assertEqual(server["name"], "本机自动化验证节点")

        host_key_data = base64.b64encode(b"integration-host-key").decode()
        _, password_connection = self.request("PUT", f"/api/v1/servers/{server_id}/connection", {
            "connection_type": "ssh",
            "host": "127.0.0.1",
            "port": 22,
            "username": "integration-user",
            "credential_ref": "agentswatch-managed",
            "ssh_password": "PersistentSshPassword!2026",
            "ssh_host_keys": [f"127.0.0.1 ssh-ed25519 {host_key_data}"],
        })
        saved_connection = password_connection["data"]["connection"]
        self.assertTrue(saved_connection["password_configured"])
        self.assertEqual(saved_connection["bootstrap_status"], "pending")
        self.assertNotIn("ssh_password", json.dumps(password_connection))
        with psycopg.connect(DATABASE_URL) as connection:
            encrypted = connection.execute(
                "SELECT encrypted_password FROM security.server_ssh_credential WHERE server_id = %s",
                (server_id,),
            ).fetchone()[0]
        self.assertNotIn("PersistentSshPassword!2026", encrypted)

        _, connected = self.request("PUT", f"/api/v1/servers/{server_id}/connection", {
            "connection_type": "local",
            "host": "127.0.0.1",
            "row_version": saved_connection["row_version"],
        })
        self.assertEqual(connected["data"]["connection"]["connection_type"], "local")

        run_status, run_body = self.request("POST", "/api/v1/automation/runs", {
            "operation_code": "server.check_connection",
            "server_ids": [server_id],
        })
        self.assertEqual(run_status, 202)
        run_id = run_body["data"]["run_id"]
        final = None
        for _ in range(120):
            _, response = self.get(f"/api/v1/automation/runs/{run_id}")
            final = response["data"]
            if final["status"] not in {"queued", "running"}:
                break
            time.sleep(0.1)
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["hosts"][0]["status"], "succeeded")
        self.assertTrue(final["log_available"])
        self.assertGreater(final["event_count"], 0)
        self.assertEqual(final["last_event_sequence"], final["event_count"])

        _, first_events = self.get(f"/api/v1/automation/runs/{run_id}/events?limit=2")
        self.assertEqual([item["sequence_no"] for item in first_events["data"]], [1, 2])
        self.assertTrue(first_events["cursor"]["has_more"])
        _, remaining_events = self.get(
            f"/api/v1/automation/runs/{run_id}/events?after_sequence={first_events['cursor']['next_sequence']}&limit=500"
        )
        all_events = first_events["data"] + remaining_events["data"]
        self.assertEqual(
            [item["sequence_no"] for item in all_events],
            sorted(item["sequence_no"] for item in all_events),
        )
        event_types = {item["event_type"] for item in all_events}
        self.assertTrue({"task_queued", "task_started", "ansible_started", "task_completed"}.issubset(event_types))
        self.assertTrue(any(value.startswith("runner_on_") for value in event_types))
        self.assertTrue(remaining_events["cursor"]["terminal"])
        _, target_events = self.get(
            f"/api/v1/automation/runs/{run_id}/events?target_key={server_id}&limit=500"
        )
        self.assertTrue(target_events["data"])
        self.assertTrue(all(item["target_key"] == server_id for item in target_events["data"]))
        serialized_events = json.dumps(all_events, ensure_ascii=False)
        for secret in ("local-app-only", "local-reader-only", "module_args", "invocation", "AGENTSWATCH_DATABASE_URL"):
            self.assertNotIn(secret, serialized_events)

        _, latest = self.get(f"/api/v1/servers/{server_id}")
        self.assertEqual(latest["data"]["connection"]["connection_status"], "reachable")
        self.assertGreaterEqual(len(latest["data"]["recent_operations"]), 1)

        _, retired = self.request(
            "DELETE", f"/api/v1/servers/{server_id}?row_version={latest['data']['row_version']}"
        )
        self.assertEqual(retired["data"]["lifecycle_status"], "retired")

    def test_automation_library_publishes_versioned_playbook_and_task_center_lists_run(self):
        _, initial = self.get("/api/v1/automation-library?limit=100")
        self.assertEqual(initial["page"]["total"], 12)
        self.assertTrue(all(item["builtin"] for item in initial["data"]))
        self.assertTrue(all(item["version_label"] for item in initial["data"]))

        playbook = """---
- name: AgentsWatch managed automation test
  hosts: all
  gather_facts: false
  tasks:
    - name: Return a controlled result
      ansible.builtin.set_fact:
        agentswatch_result:
          managed_automation: true
          message: "{{ message }}"
"""
        create_status, created = self.request("POST", "/api/v1/automation-library", {
            "code": "server.managed_message",
            "name": "输出受控消息",
            "description": "API 集成测试创建的独立 Playbook。",
            "category": "本机验证",
            "scope": "server",
            "risk_level": "low",
            "supports_batch": True,
            "timeout_seconds": 30,
            "version_label": "1.0.0",
            "playbook_content": playbook,
            "parameter_schema": {
                "message": {
                    "type": "string", "required": True,
                    "label": "消息", "max_length": 100,
                },
            },
        })
        self.assertEqual(create_status, 201)
        automation = created["data"]
        automation_id = automation["automation_id"]
        draft = automation["draft_version"]
        self.assertEqual(automation["lifecycle_status"], "draft")

        _, validation = self.request(
            "POST",
            f"/api/v1/automation-library/{automation_id}/versions/{draft['automation_version_id']}/validate",
            {},
        )
        self.assertEqual(validation["data"]["validation_status"], "passed")
        _, published = self.request(
            "POST",
            f"/api/v1/automation-library/{automation_id}/versions/{draft['automation_version_id']}/publish",
            {},
        )
        self.assertEqual(published["data"]["current_version"]["version_label"], "1.0.0")
        self.assertIsNone(published["data"]["draft_version"])

        _, versioned = self.request(
            "POST", f"/api/v1/automation-library/{automation_id}/versions",
            {"version_label": "1.1.0"},
        )
        next_draft = versioned["data"]["draft_version"]
        updated_playbook = playbook.replace("Return a controlled result", "Return an updated controlled result")
        _, updated = self.request(
            "PATCH",
            f"/api/v1/automation-library/{automation_id}/versions/{next_draft['automation_version_id']}",
            {
                "row_version": versioned["data"]["row_version"],
                "version_row_version": next_draft["row_version"],
                "description": "已通过在线草稿编辑更新。",
                "playbook_content": updated_playbook,
                "parameter_schema": next_draft["parameter_schema"],
            },
        )
        next_draft = updated["data"]["draft_version"]
        self.assertEqual(next_draft["validation_status"], "pending")
        _, validation = self.request(
            "POST",
            f"/api/v1/automation-library/{automation_id}/versions/{next_draft['automation_version_id']}/validate",
            {},
        )
        self.assertEqual(validation["data"]["validation_status"], "passed")
        _, republished = self.request(
            "POST",
            f"/api/v1/automation-library/{automation_id}/versions/{next_draft['automation_version_id']}/publish",
            {},
        )
        self.assertEqual(republished["data"]["current_version"]["version_label"], "1.1.0")
        self.assertEqual(len(republished["data"]["versions"]), 2)

        _, catalog = self.get("/api/v1/automation/operations")
        managed = next(
            item for item in catalog["data"]["operations"]
            if item["code"] == "server.managed_message"
        )
        self.assertEqual(managed["version"], "1.1.0")

        run_status, started = self.request("POST", "/api/v1/automation/runs", {
            "operation_code": "server.managed_message",
            "server_ids": ["00000000-0000-4000-8000-000000000501"],
            "parameters": {"message": "自动化库已发布"},
        })
        self.assertEqual(run_status, 202)
        final = self.wait_run(started["data"]["run_id"])
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["version_label"], "1.1.0")
        self.assertTrue(final["hosts"][0]["result"]["managed_automation"])

        _, tasks = self.get("/api/v1/automation/runs?operation_code=server.managed_message")
        self.assertEqual(tasks["page"]["total"], 1)
        self.assertEqual(tasks["data"][0]["operation_name"], "输出受控消息")
        self.assertGreaterEqual(tasks["summary"]["succeeded"], 1)

        _, detail = self.get(f"/api/v1/automation-library/{automation_id}")
        automation_detail = detail["data"]
        self.assertEqual(automation_detail["current_version"]["playbook_content"], updated_playbook)
        self.assertEqual(
            automation_detail["current_version"]["parameter_schema"]["message"]["type"],
            "string",
        )
        self.assertEqual(automation_detail["run_summary"]["total"], 1)
        self.assertEqual(automation_detail["run_summary"]["succeeded"], 1)
        self.assertEqual(automation_detail["recent_runs"][0]["run_id"], final["run_id"])
        self.assertTrue(automation_detail["recent_runs"][0]["playbook_version"].startswith("1.1.0+"))

    def test_runtime_list_and_single_primary_runtime_detail(self):
        _, listed = self.get("/api/v1/runtimes?family_code=openclaw")
        _, detail = self.get("/api/v1/runtimes/00000000-0000-4000-8000-000000000701")

        self.assertEqual(listed["page"]["total"], 2)
        listed_runtime = next(
            item for item in listed["data"]
            if item["runtime_id"] == "00000000-0000-4000-8000-000000000701"
        )
        self.assertEqual(listed_runtime["active_primary_agents"], 1)
        self.assertEqual(listed_runtime["lobster_name"], "员工个人数字助手")
        self.assertEqual(listed_runtime["server_display_id"], "ECS-001")
        self.assertEqual(listed_runtime["gateway_id"], "gateway-701")
        self.assertEqual(detail["data"]["management_driver"], "systemd_user")
        self.assertEqual(detail["data"]["command_path"], "/home/openclaw/.local/bin/openclaw")
        self.assertEqual(len(detail["data"]["active_deployments"]), 1)
        self.assertEqual(detail["data"]["latest_snapshot"]["actual_version"], "2026.6.11")

    def test_runtime_management_supports_baremetal_operations_and_container_asset_option(self):
        create_status, created = self.request("POST", "/api/v1/runtimes", {
            "server_id": "00000000-0000-4000-8000-000000000501",
            "runtime_family_id": "00000000-0000-4000-8000-000000000601",
            "name": "本机裸金属运行时",
            "deployment_mode": "baremetal",
            "lifecycle_status": "active",
            "runtime_account": "openclaw",
            "config_path": "/tmp/openclaw-test",
            "desired_version": "2026.7.1",
            "management_driver": "mock",
            "management_target": "openclaw-local-test",
        })
        self.assertEqual(create_status, 201)
        runtime = created["data"]
        runtime_id = runtime["runtime_id"]

        _, updated = self.request("PATCH", f"/api/v1/runtimes/{runtime_id}", {
            "row_version": runtime["row_version"],
            "name": "本机裸金属运行时（已更新）",
            "desired_version": "2026.7.2",
        })
        runtime = updated["data"]
        self.assertEqual(runtime["desired_version"], "2026.7.2")

        for expected_created in (True, False):
            collect_status, collecting = self.request("POST", "/api/v1/automation/runs", {
                "operation_code": "runtime.collect_info",
                "runtime_ids": [runtime_id],
            })
            self.assertEqual(collect_status, 202)
            collected = self.wait_run(collecting["data"]["run_id"])
            self.assertEqual(collected["status"], "succeeded")
            collection = collected["hosts"][0]["result"]
            self.assertEqual(collection["actual_version"], "1.0.0")
            self.assertEqual(collection["snapshot_created"], expected_created)
            self.assertIsNone(collection["observed_agent_count"])

        _, discovered = self.get(f"/api/v1/runtimes/{runtime_id}")
        self.assertEqual(discovered["data"]["latest_snapshot"]["actual_version"], "1.0.0")
        self.assertEqual(len(discovered["data"]["recent_snapshots"]), 1)
        self.assertIsNotNone(discovered["data"]["last_discovered_at"])

        run_status, started = self.request("POST", "/api/v1/automation/runs", {
            "operation_code": "runtime.stop",
            "runtime_ids": [runtime_id],
        })
        self.assertEqual(run_status, 202)
        run_id = started["data"]["run_id"]
        final = None
        for _ in range(120):
            _, response = self.get(f"/api/v1/automation/runs/{run_id}")
            final = response["data"]
            if final["status"] not in {"queued", "running"}:
                break
            time.sleep(0.1)
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["hosts"][0]["runtime_id"], runtime_id)
        _, stopped = self.get(f"/api/v1/runtimes/{runtime_id}")
        self.assertEqual(stopped["data"]["observed_status"], "stopped")
        self.assertGreaterEqual(len(stopped["data"]["recent_operations"]), 1)

        _, retired = self.request(
            "DELETE", f"/api/v1/runtimes/{runtime_id}?row_version={stopped['data']['row_version']}"
        )
        self.assertEqual(retired["data"]["lifecycle_status"], "retired")

        container_status, container = self.request("POST", "/api/v1/runtimes", {
            "server_id": "00000000-0000-4000-8000-000000000502",
            "runtime_family_id": "00000000-0000-4000-8000-000000000602",
            "name": "容器运行时资产占位",
            "deployment_mode": "container",
            "lifecycle_status": "planned",
            "container_name": "future-hermes",
            "image_ref": "registry.example/hermes:future",
            "management_driver": "none",
        })
        self.assertEqual(container_status, 201)
        self.assertEqual(container["data"]["deployment_mode"], "container")
        self.assertEqual(container["data"]["management_driver"], "none")

    def test_runtime_webui_endpoint_encrypts_credentials_and_launches_local_access_session(self):
        class MockWebUiHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"mock runtime web ui"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        web_ui = ThreadingHTTPServer(("127.0.0.1", 0), MockWebUiHandler)
        thread = threading.Thread(target=web_ui.serve_forever, daemon=True)
        thread.start()
        port = web_ui.server_address[1]
        session_id = None
        try:
            _, created = self.request("POST", "/api/v1/runtimes", {
                "server_id": "00000000-0000-4000-8000-000000000501",
                "runtime_family_id": "00000000-0000-4000-8000-000000000601",
                "name": "本机 WebUI 验证运行时",
                "deployment_mode": "baremetal",
                "lifecycle_status": "active",
                "endpoint_port": port,
            })
            runtime_id = created["data"]["runtime_id"]

            save_status, saved = self.request("PUT", f"/api/v1/runtimes/{runtime_id}/web-endpoint", {
                "display_name": "OpenClaw Control UI",
                "access_mode": "local_ssh_tunnel",
                "scheme": "http",
                "remote_host": "127.0.0.1",
                "base_path": "/",
                "auth_mode": "token",
                "token": "saved-openclaw-token",
                "enabled": True,
            })
            self.assertEqual(save_status, 200)
            endpoint = saved["data"]
            self.assertTrue(endpoint["credential_configured"])
            self.assertNotIn("token", endpoint)
            self.assertNotIn("encrypted_credential", endpoint)
            self.assertEqual(endpoint["effective_port"], port)

            launch_status, launched = self.request(
                "POST", f"/api/v1/runtimes/{runtime_id}/web-access-sessions", {}
            )
            self.assertEqual(launch_status, 201)
            access = launched["data"]
            session_id = access["access_session_id"]
            parsed = urlsplit(access["launch_url"])
            self.assertEqual(parsed.hostname, "127.0.0.1")
            self.assertEqual(parsed.port, port)
            self.assertEqual(parsed.fragment, "token=saved-openclaw-token")
            self.assertIsNone(access["manual_credential"])
            with urlopen(access["launch_url"].split("#", 1)[0], timeout=3) as response:
                self.assertEqual(response.read(), b"mock runtime web ui")

            _, closed = self.request("DELETE", f"/api/v1/runtime-web-access-sessions/{session_id}")
            self.assertEqual(closed["data"]["status"], "closed")
            session_id = None

            _, detail = self.get(f"/api/v1/runtimes/{runtime_id}")
            self.assertEqual(detail["data"]["web_endpoint"]["auth_mode"], "token")
            self.assertGreaterEqual(len(detail["data"]["recent_web_access_sessions"]), 1)

            _, saved_login = self.request("PUT", f"/api/v1/runtimes/{runtime_id}/web-endpoint", {
                "display_name": "通用运行时 WebUI",
                "access_mode": "local_ssh_tunnel",
                "scheme": "http",
                "remote_host": "127.0.0.1",
                "base_path": "/",
                "auth_mode": "username_password",
                "username": "runtime-admin",
                "password": "saved-runtime-password",
                "enabled": True,
                "row_version": detail["data"]["web_endpoint"]["row_version"],
            })
            self.assertTrue(saved_login["data"]["credential_configured"])
            self.assertNotIn("password", saved_login["data"])

            _, login_launch = self.request(
                "POST", f"/api/v1/runtimes/{runtime_id}/web-access-sessions", {}
            )
            session_id = login_launch["data"]["access_session_id"]
            self.assertEqual(login_launch["data"]["manual_credential"], {
                "auth_mode": "username_password",
                "username": "runtime-admin",
                "password": "saved-runtime-password",
            })
            self.assertNotIn("saved-runtime-password", login_launch["data"]["launch_url"])
            _, closed = self.request("DELETE", f"/api/v1/runtime-web-access-sessions/{session_id}")
            self.assertEqual(closed["data"]["status"], "closed")
            session_id = None
        finally:
            if session_id:
                try:
                    self.request("DELETE", f"/api/v1/runtime-web-access-sessions/{session_id}")
                except Exception:
                    pass
            web_ui.shutdown()
            web_ui.server_close()

    def test_agent_filters_and_relationship_detail(self):
        _, listed = self.get("/api/v1/agents?structure_role=primary&service_archetype=business")
        _, detail = self.get("/api/v1/agents/00000000-0000-4000-8000-000000000901")

        self.assertEqual(listed["page"]["total"], 2)
        self.assertEqual(len(detail["data"]["subagents"]), 1)
        self.assertEqual(len(detail["data"]["accessible_people"]), 3)
        self.assertIsNotNone(detail["data"]["active_deployment"])

    def test_z_agent_management_crud_and_feishu_channel_are_secret_safe(self):
        create_status, created = self.request("POST", "/api/v1/agents", {
            "name": "本机法律渠道验证智能体",
            "structure_role": "primary",
            "service_archetype": "business",
            "lifecycle_status": "requested",
            "owning_department_id": "00000000-0000-4000-8000-000000000203",
            "business_admin_person_id": "00000000-0000-4000-8000-000000000301",
            "it_admin_person_id": "00000000-0000-4000-8000-000000000302",
            "business_domain": "法律服务",
            "business_scenario": "合同渠道验证",
            "accessible_person_ids": [
                "00000000-0000-4000-8000-000000000305",
                "00000000-0000-4000-8000-000000000306",
            ],
            "group_ids": ["00000000-0000-4000-8000-000000000803"],
            "channel": {
                "channel_type": "feishu",
                "app_id": "cli_agentswatch_test_001",
                "app_secret": "super-secret-value",
            },
        })
        self.assertEqual(create_status, 201)
        agent = created["data"]
        agent_id = agent["agent_id"]
        self.assertEqual(agent["channel"]["channel_type"], "feishu")
        self.assertEqual(agent["channel"]["application_name"], agent["name"])
        self.assertEqual(agent["channel"]["app_id"], "cli_agentswatch_test_001")
        self.assertTrue(agent["channel"]["credential_configured"])
        serialized = json.dumps(created, ensure_ascii=False)
        self.assertNotIn("super-secret-value", serialized)
        self.assertNotIn("encrypted_app_secret", serialized)
        self.assertEqual(len(agent["accessible_people"]), 2)
        self.assertEqual(len(agent["groups"]), 1)

        _, renamed = self.request("PATCH", f"/api/v1/agents/{agent_id}", {
            "row_version": agent["row_version"],
            "name": "本机法律渠道验证智能体（改名）",
            "channel": {
                "channel_type": "feishu",
                "application_name": "法律渠道应用",
                "app_id": "cli_agentswatch_test_001",
            },
        })
        agent = renamed["data"]
        self.assertEqual(agent["channel"]["application_name"], "法律渠道应用")
        self.assertTrue(agent["channel"]["credential_configured"])

        with self.assertRaises(HTTPError) as duplicate:
            self.request("POST", "/api/v1/agents", {
                "name": "重复飞书 AppID 智能体",
                "structure_role": "primary",
                "service_archetype": "business",
                "lifecycle_status": "requested",
                "business_domain": "测试",
                "business_scenario": "重复 AppID",
                "channel": {
                    "channel_type": "feishu",
                    "app_id": "cli_agentswatch_test_001",
                    "app_secret": "another-secret",
                },
            })
        self.assertEqual(duplicate.exception.code, 409)

        child_status, child_created = self.request("POST", "/api/v1/agents", {
            "name": "合同检索子智能体",
            "structure_role": "subagent",
            "parent_agent_id": agent_id,
            "service_archetype": "business",
            "lifecycle_status": "requested",
            "channel": {
                "channel_type": "feishu",
                "app_id": "cli_agentswatch_child_001",
                "app_secret": "child-secret-value",
            },
        })
        self.assertEqual(child_status, 201)
        child = child_created["data"]
        self.assertEqual(child["channel"]["channel_type"], "feishu")
        self.assertEqual(child["channel"]["app_id"], "cli_agentswatch_child_001")
        self.assertEqual(child["channel"]["application_name"], child["name"])
        self.assertNotIn("encrypted_app_secret", json.dumps(child))
        self.assertNotIn("child-secret-value", json.dumps(child))

        with self.assertRaises(HTTPError) as duplicate_child_channel:
            self.request("POST", "/api/v1/agents", {
                "name": "重复渠道子智能体",
                "structure_role": "subagent",
                "parent_agent_id": agent_id,
                "service_archetype": "business",
                "lifecycle_status": "requested",
                "channel": {
                    "channel_type": "feishu",
                    "app_id": "cli_agentswatch_child_001",
                    "app_secret": "duplicate-child-secret",
                },
            })
        self.assertEqual(duplicate_child_channel.exception.code, 409)

        _, retired_child = self.request(
            "DELETE", f"/api/v1/agents/{child['agent_id']}?row_version={child['row_version']}"
        )
        self.assertEqual(retired_child["data"]["lifecycle_status"], "retired")

        _, no_channel = self.request("PATCH", f"/api/v1/agents/{agent_id}", {
            "row_version": agent["row_version"],
            "channel": {"channel_type": "none"},
        })
        agent = no_channel["data"]
        self.assertEqual(agent["channel"]["channel_type"], "none")

        _, retired = self.request(
            "DELETE", f"/api/v1/agents/{agent_id}?row_version={agent['row_version']}"
        )
        self.assertEqual(retired["data"]["lifecycle_status"], "retired")

    def test_zz_agent_deployment_uses_isolated_openclaw_and_confirms_relationships(self):
        with tempfile.TemporaryDirectory(prefix="agentswatch-api-agent-") as directory:
            root = Path(directory)
            _, runtime_response = self.request("POST", "/api/v1/runtimes", {
                "server_id": "00000000-0000-4000-8000-000000000501",
                "runtime_family_id": "00000000-0000-4000-8000-000000000601",
                "name": "智能体部署验证运行时",
                "deployment_mode": "baremetal",
                "lifecycle_status": "active",
                "runtime_account": "openclaw",
                "config_path": str(root / "openclaw.json"),
                "state_path": str(root),
                "management_driver": "mock",
                "management_target": "agentswatch-agent-deployment-test",
            })
            runtime_id = runtime_response["data"]["runtime_id"]

            _, primary_response = self.request("POST", "/api/v1/agents", {
                "name": "部署验证主智能体",
                "structure_role": "primary",
                "service_archetype": "business",
                "lifecycle_status": "testing",
                "business_admin_person_id": "00000000-0000-4000-8000-000000000301",
                "it_admin_person_id": "00000000-0000-4000-8000-000000000302",
                "business_domain": "部署验证",
                "business_scenario": "OpenClaw Playbook",
                "channel": {
                    "channel_type": "feishu",
                    "app_id": "cli_deployment_primary",
                    "app_secret": "deployment-primary-secret",
                },
            })
            primary_id = primary_response["data"]["agent_id"]
            _, child_response = self.request("POST", "/api/v1/agents", {
                "name": "部署验证子智能体",
                "structure_role": "subagent",
                "parent_agent_id": primary_id,
                "service_archetype": "business",
                "lifecycle_status": "testing",
                "channel": {
                    "channel_type": "feishu",
                    "app_id": "cli_deployment_child",
                    "app_secret": "deployment-child-secret",
                },
            })
            child_id = child_response["data"]["agent_id"]

            payload = {
                "operation_code": "agent.apply_deployment",
                "agent_ids": [primary_id],
                "parameters": {
                    "runtime_id": runtime_id,
                    "runtime_agent_key": "agentswatch-api-main",
                    "workspace_root": str(root / "workspaces"),
                },
            }
            deploy_status, deployment = self.request("POST", "/api/v1/automation/runs", payload)
            self.assertEqual(deploy_status, 202)
            first = self.wait_run(deployment["data"]["run_id"])
            self.assertEqual(first["status"], "succeeded", first)
            self.assertEqual(first["hosts"][0]["agent_id"], primary_id)
            self.assertEqual(first["hosts"][0]["result"]["created_agent_count"], 2)
            self.assertEqual(len(first["hosts"][0]["result"]["configured_channel_account_ids"]), 2)
            first_plan = first["hosts"][0]["target_plan"]
            self.assertEqual(
                {item["agent_id"] for item in first_plan["definitions"]},
                {primary_id, child_id},
            )
            self.assertNotEqual(first_plan, first["hosts"][0]["result"])
            self.assertNotIn("deployment-primary-secret", json.dumps(first))
            self.assertNotIn("deployment-child-secret", json.dumps(first))

            _, occupied_primary_response = self.request("POST", "/api/v1/agents", {
                "name": "运行时占用冲突验证主智能体",
                "structure_role": "primary",
                "service_archetype": "business",
                "lifecycle_status": "testing",
                "business_admin_person_id": "00000000-0000-4000-8000-000000000301",
                "it_admin_person_id": "00000000-0000-4000-8000-000000000302",
                "business_domain": "部署验证",
                "business_scenario": "运行时一对一约束",
            })
            occupied_payload = {
                **payload,
                "agent_ids": [occupied_primary_response["data"]["agent_id"]],
                "parameters": {**payload["parameters"], "runtime_agent_key": "occupied-main"},
            }
            with self.assertRaises(HTTPError) as occupied_error:
                self.request("POST", "/api/v1/automation/runs", occupied_payload)
            self.assertEqual(occupied_error.exception.code, 409)

            _, detail_response = self.get(f"/api/v1/agents/{primary_id}")
            detail = detail_response["data"]
            self.assertEqual(detail["active_deployment"]["runtime_id"], runtime_id)
            self.assertEqual(len(detail["deployment_members"]), 2)
            self.assertEqual(detail["deployment_members"][0]["agent_id"], primary_id)
            self.assertEqual(detail["deployment_members"][1]["agent_id"], child_id)
            self.assertTrue(all(item["mapping_status"] == "verified" for item in detail["deployment_members"]))
            self.assertGreaterEqual(len(detail["recent_operations"]), 1)

            config = json.loads((root / "openclaw.json").read_text(encoding="utf-8"))
            primary_account = f"aw-{primary_id.replace('-', '')}"
            child_account = f"aw-{child_id.replace('-', '')}"
            self.assertEqual(config["channels"]["feishu"]["accounts"][primary_account]["appId"], "cli_deployment_primary")
            self.assertEqual(config["channels"]["feishu"]["accounts"][child_account]["appId"], "cli_deployment_child")
            self.assertEqual({item["match"]["accountId"] for item in config["bindings"]}, {primary_account, child_account})

            _, child_detail = self.get(f"/api/v1/agents/{child_id}")
            _, retired_child = self.request(
                "DELETE",
                f"/api/v1/agents/{child_id}?row_version={child_detail['data']['row_version']}",
            )
            self.assertEqual(retired_child["data"]["lifecycle_status"], "retired")

            _, second_response = self.request("POST", "/api/v1/automation/runs", payload)
            second = self.wait_run(second_response["data"]["run_id"])
            self.assertEqual(second["status"], "succeeded", second)
            self.assertEqual(second["hosts"][0]["result"]["created_agent_count"], 0)
            self.assertEqual(second["hosts"][0]["result"]["retired_agent_keys"], [detail["deployment_members"][1]["runtime_agent_key"]])
            self.assertEqual(
                [item["agent_id"] for item in second["hosts"][0]["target_plan"]["retired_definitions"]],
                [child_id],
            )

            _, synchronized_response = self.get(f"/api/v1/agents/{primary_id}")
            synchronized = synchronized_response["data"]
            self.assertEqual(len(synchronized["deployment_members"]), 1)
            config = json.loads((root / "openclaw.json").read_text(encoding="utf-8"))
            self.assertIn(primary_account, config["channels"]["feishu"]["accounts"])
            self.assertNotIn(child_account, config["channels"]["feishu"]["accounts"])
            self.assertEqual([item["match"]["accountId"] for item in config["bindings"]], [primary_account])
            self.assertIn(
                detail["deployment_members"][1]["runtime_agent_key"],
                [item["id"] for item in config["agents"]["list"]],
            )

            target_root = root / "migration-target"
            _, target_runtime_response = self.request("POST", "/api/v1/runtimes", {
                "server_id": "00000000-0000-4000-8000-000000000501",
                "runtime_family_id": "00000000-0000-4000-8000-000000000601",
                "name": "智能体迁移目标运行时",
                "deployment_mode": "baremetal",
                "lifecycle_status": "active",
                "runtime_account": "openclaw",
                "config_path": str(target_root / "openclaw.json"),
                "state_path": str(target_root),
                "management_driver": "mock",
                "management_target": "agentswatch-agent-migration-target",
            })
            target_runtime_id = target_runtime_response["data"]["runtime_id"]
            source_deployment_id = synchronized["active_deployment"]["agent_deployment_id"]
            stable_runtime_key = synchronized["deployment_members"][0]["runtime_agent_key"]
            _, migrate_response = self.request("POST", "/api/v1/automation/runs", {
                "operation_code": "agent.migrate_deployment",
                "agent_ids": [primary_id],
                "parameters": {
                    "runtime_id": target_runtime_id,
                    "expected_deployment_id": source_deployment_id,
                    "runtime_agent_key": stable_runtime_key,
                    "workspace_root": str(target_root / "workspaces"),
                },
            })
            migrated_run = self.wait_run(migrate_response["data"]["run_id"])
            self.assertEqual(migrated_run["status"], "succeeded", migrated_run)
            migrated_result = migrated_run["hosts"][0]["result"]
            self.assertTrue(migrated_result["relationship_switched"])
            self.assertTrue(migrated_result["retained_source_workspaces"])

            _, migrated_detail_response = self.get(f"/api/v1/agents/{primary_id}")
            migrated_detail = migrated_detail_response["data"]
            self.assertEqual(migrated_detail["active_deployment"]["runtime_id"], target_runtime_id)
            self.assertEqual(
                migrated_detail["deployment_members"][0]["runtime_agent_key"], stable_runtime_key,
            )
            self.assertEqual(
                migrated_detail["deployment_members"][0]["config_ref"],
                str(target_root / "workspaces" / stable_runtime_key),
            )
            with psycopg.connect(DATABASE_URL) as connection:
                old_lifecycle, old_valid_to = connection.execute(
                    "SELECT lifecycle_status::text, valid_to FROM asset.agent_deployment WHERE agent_deployment_id=%s",
                    (source_deployment_id,),
                ).fetchone()
                self.assertEqual(old_lifecycle, "inactive")
                self.assertIsNotNone(old_valid_to)
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM asset.agent_deployment_member WHERE agent_deployment_id=%s AND valid_to IS NULL",
                    (source_deployment_id,),
                ).fetchone()[0], 0)
            source_config = json.loads((root / "openclaw.json").read_text(encoding="utf-8"))
            target_config = json.loads((target_root / "openclaw.json").read_text(encoding="utf-8"))
            self.assertNotIn(primary_account, source_config["channels"]["feishu"].get("accounts", {}))
            self.assertTrue(target_config["channels"]["feishu"]["accounts"][primary_account]["enabled"])
            self.assertIn(stable_runtime_key, [item["id"] for item in source_config["agents"]["list"]])

            target_deployment_id = migrated_detail["active_deployment"]["agent_deployment_id"]
            _, remove_response = self.request("POST", "/api/v1/automation/runs", {
                "operation_code": "agent.remove_deployment",
                "agent_ids": [primary_id],
                "parameters": {"expected_deployment_id": target_deployment_id},
            })
            removed_run = self.wait_run(remove_response["data"]["run_id"])
            self.assertEqual(removed_run["status"], "succeeded", removed_run)
            self.assertTrue(removed_run["hosts"][0]["result"]["relationship_removed"])
            _, removed_detail_response = self.get(f"/api/v1/agents/{primary_id}")
            self.assertIsNone(removed_detail_response["data"]["active_deployment"])
            target_config = json.loads((target_root / "openclaw.json").read_text(encoding="utf-8"))
            self.assertNotIn(primary_account, target_config["channels"]["feishu"].get("accounts", {}))
            self.assertIn(stable_runtime_key, [item["id"] for item in target_config["agents"]["list"]])

            imported_existing_key = "imported-unreserved-main"
            imported_existing_workspace = target_root / "existing-import-workspace"
            subprocess.run(
                [
                    "openclaw", "agents", "add", imported_existing_key,
                    "--non-interactive", "--workspace",
                    str(imported_existing_workspace), "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "OPENCLAW_CONFIG_PATH": str(target_root / "openclaw.json"),
                    "OPENCLAW_STATE_DIR": str(target_root),
                },
            )

            _, imported_primary_response = self.request("POST", "/api/v1/agents", {
                "name": "导入待解析主智能体",
                "structure_role": "primary",
                "service_archetype": "business",
                "lifecycle_status": "testing",
                "business_admin_person_id": "00000000-0000-4000-8000-000000000301",
                "it_admin_person_id": "00000000-0000-4000-8000-000000000302",
                "business_domain": "部署验证",
                "business_scenario": "导入映射收编",
            })
            imported_primary_id = imported_primary_response["data"]["agent_id"]
            with psycopg.connect(DATABASE_URL) as connection:
                imported_deployment_id = connection.execute(
                    """
                    INSERT INTO asset.agent_deployment (
                        primary_agent_id, runtime_id, lifecycle_status
                    ) VALUES (%s, %s, 'active')
                    RETURNING agent_deployment_id
                    """,
                    (imported_primary_id, target_runtime_id),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO asset.agent_deployment_member (
                        agent_deployment_id, runtime_id, agent_id, member_role,
                        runtime_agent_key, mapping_status, config_ref
                    ) VALUES (%s, %s, %s, 'primary', 'candidate-wrong-key', 'unresolved', NULL)
                    """,
                    (imported_deployment_id, target_runtime_id, imported_primary_id),
                )

            _, adoption_response = self.request("POST", "/api/v1/automation/runs", {
                "operation_code": "agent.apply_deployment",
                "agent_ids": [imported_primary_id],
                "parameters": {
                    "runtime_id": target_runtime_id,
                    "runtime_agent_key": imported_existing_key,
                    "workspace_root": str(target_root / "guessed-import-workspaces"),
                },
            })
            adoption_run = self.wait_run(adoption_response["data"]["run_id"])
            self.assertEqual(adoption_run["status"], "succeeded", adoption_run)
            self.assertEqual(
                adoption_run["hosts"][0]["target_plan"]["require_existing_agent_keys"],
                [imported_existing_key],
            )
            self.assertEqual(adoption_run["hosts"][0]["result"]["created_agent_count"], 0)
            _, adopted_detail_response = self.get(f"/api/v1/agents/{imported_primary_id}")
            adopted_detail = adopted_detail_response["data"]
            adopted_member = adopted_detail["deployment_members"][0]
            self.assertEqual(adopted_member["mapping_status"], "verified")
            self.assertEqual(adopted_member["runtime_agent_key"], imported_existing_key)
            self.assertEqual(
                adopted_member["config_ref"],
                str(imported_existing_workspace),
            )

            _, adopted_remove_response = self.request("POST", "/api/v1/automation/runs", {
                "operation_code": "agent.remove_deployment",
                "agent_ids": [imported_primary_id],
                "parameters": {"expected_deployment_id": str(imported_deployment_id)},
            })
            adopted_remove_run = self.wait_run(adopted_remove_response["data"]["run_id"])
            self.assertEqual(adopted_remove_run["status"], "succeeded", adopted_remove_run)

    def test_zzz_unmanaged_import_relationship_removal_is_metadata_only(self):
        with tempfile.TemporaryDirectory(prefix="agentswatch-api-unmanaged-agent-") as directory:
            root = Path(directory)
            config_path = root / "openclaw.json"
            _, runtime_response = self.request("POST", "/api/v1/runtimes", {
                "server_id": "00000000-0000-4000-8000-000000000501",
                "runtime_family_id": "00000000-0000-4000-8000-000000000601",
                "name": "未受管导入关系运行时",
                "deployment_mode": "baremetal",
                "lifecycle_status": "active",
                "runtime_account": "openclaw",
                "config_path": str(config_path),
                "state_path": str(root),
            })
            runtime_id = runtime_response["data"]["runtime_id"]
            _, primary_response = self.request("POST", "/api/v1/agents", {
                "name": "仅解除元数据主智能体",
                "structure_role": "primary",
                "service_archetype": "business",
                "lifecycle_status": "testing",
                "business_admin_person_id": "00000000-0000-4000-8000-000000000301",
                "it_admin_person_id": "00000000-0000-4000-8000-000000000302",
                "business_domain": "部署验证",
                "business_scenario": "未受管关系解除",
            })
            primary_id = primary_response["data"]["agent_id"]
            with psycopg.connect(DATABASE_URL) as connection:
                deployment_id = connection.execute(
                    """
                    INSERT INTO asset.agent_deployment (
                        primary_agent_id, runtime_id, lifecycle_status
                    ) VALUES (%s, %s, 'active')
                    RETURNING agent_deployment_id
                    """,
                    (primary_id, runtime_id),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO asset.agent_deployment_member (
                        agent_deployment_id, runtime_id, agent_id, member_role,
                        runtime_agent_key, mapping_status, config_ref
                    ) VALUES (%s, %s, %s, 'primary', NULL, 'unresolved', NULL)
                    """,
                    (deployment_id, runtime_id, primary_id),
                )

            _, remove_response = self.request("POST", "/api/v1/automation/runs", {
                "operation_code": "agent.remove_deployment",
                "agent_ids": [primary_id],
                "parameters": {"expected_deployment_id": str(deployment_id)},
            })
            removed_run = self.wait_run(remove_response["data"]["run_id"])
            self.assertEqual(removed_run["status"], "succeeded", removed_run)
            result = removed_run["hosts"][0]["result"]
            self.assertTrue(result["relationship_removed"])
            self.assertTrue(result["remote_cleanup_skipped"])
            self.assertTrue(result["remote_cleanup_not_required"])
            self.assertTrue(result["unmanaged_mapping"])
            self.assertFalse(config_path.exists())
            _, detail_response = self.get(f"/api/v1/agents/{primary_id}")
            self.assertIsNone(detail_response["data"]["active_deployment"])
            with psycopg.connect(DATABASE_URL) as connection:
                lifecycle, valid_to = connection.execute(
                    """
                    SELECT lifecycle_status::text, valid_to
                    FROM asset.agent_deployment
                    WHERE agent_deployment_id = %s
                    """,
                    (deployment_id,),
                ).fetchone()
                self.assertEqual(lifecycle, "inactive")
                self.assertIsNotNone(valid_to)

    def test_deployments_groups_imports_and_quality_issues_are_queryable(self):
        _, deployments = self.get("/api/v1/deployments?lifecycle_status=active")
        _, groups = self.get("/api/v1/groups?resource_type=server")
        _, imports = self.get("/api/v1/imports")
        _, issues = self.get("/api/v1/data-quality/issues?severity=warning")

        self.assertEqual(deployments["page"]["total"], 2)
        self.assertEqual(groups["page"]["total"], 2)
        self.assertEqual(imports["page"]["total"], 1)
        self.assertEqual(issues["page"]["total"], 1)
        self.assertEqual(issues["data"][0]["issue_code"], "EMPTY_MEANS_IGNORE")
        self.assertEqual(issues["data"][0]["row_status"], "unchanged")
        self.assertEqual(issues["data"][0]["target_object_type"], "server_asset")
        self.assertEqual(issues["data"][0]["raw_payload"]["name"], "")

    def test_group_detail_returns_type_specific_current_members(self):
        _, server_group = self.get("/api/v1/groups/00000000-0000-4000-8000-000000000801")
        _, agent_group = self.get("/api/v1/groups/00000000-0000-4000-8000-000000000803")

        self.assertEqual(server_group["data"]["resource_type"], "server")
        self.assertEqual(server_group["data"]["current_members"], 1)
        self.assertEqual(server_group["data"]["members"][0]["name"], "容器宿主机")
        self.assertEqual(agent_group["data"]["resource_type"], "agent")
        self.assertEqual(agent_group["data"]["current_members"], 1)
        self.assertEqual(agent_group["data"]["members"][0]["name"], "财务部综合助手")

    def test_import_detail_contains_rows_issues_and_field_changes(self):
        _, body = self.get("/api/v1/imports/00000000-0000-4000-8000-000000001401")
        item = body["data"]

        self.assertEqual(len(item["rows"]), 1)
        self.assertEqual(len(item["issues"]), 1)
        self.assertEqual(len(item["changes"]), 1)

    def test_unknown_asset_returns_404_and_invalid_write_is_rejected(self):
        with self.assertRaises(HTTPError) as missing:
            self.get("/api/v1/servers/00000000-0000-4000-8000-999999999999")
        self.assertEqual(missing.exception.code, 404)

        with self.assertRaises(HTTPError) as missing_group:
            self.get("/api/v1/groups/00000000-0000-4000-8000-999999999999")
        self.assertEqual(missing_group.exception.code, 404)

        request = Request(f"{BASE_URL}/api/v1/servers", data=b"{}", method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("X-CSRF-Token", self.csrf_token())
        with self.assertRaises(HTTPError) as write:
            self.opener.open(request, timeout=5)
        self.assertEqual(write.exception.code, 422)

    def test_zz_organization_management_crud_constraints_and_audit(self):
        suffix = str(int(time.time() * 1000))
        root_code = f"ORG-ROOT-{suffix}"
        child_code = f"ORG-CHILD-{suffix}"
        employee_no = f"EMP-{suffix}"

        create_status, root_body = self.request("POST", "/api/v1/departments", {
            "department_code": root_code,
            "name": "组织管理验证总部",
        })
        self.assertEqual(create_status, 201)
        root = root_body["data"]
        root_id = root["department_id"]
        self.assertEqual(root["department_code"], root_code)

        _, child_body = self.request("POST", "/api/v1/departments", {
            "department_code": child_code,
            "name": "组织管理验证子部门",
            "parent_department_id": root_id,
        })
        child = child_body["data"]
        child_id = child["department_id"]
        _, grandchild_body = self.request("POST", "/api/v1/departments", {
            "department_code": f"ORG-GRANDCHILD-{suffix}",
            "name": "组织管理验证三级部门",
            "parent_department_id": child_id,
        })
        grandchild = grandchild_body["data"]
        grandchild_id = grandchild["department_id"]
        _, person_body = self.request("POST", "/api/v1/people", {
            "employee_no": employee_no,
            "display_name": "组织管理验证人员",
            "primary_department_id": child_id,
        })
        person = person_body["data"]
        person_id = person["person_id"]

        _, department_list = self.get(f"/api/v1/departments?q={root_code}&is_active=true")
        _, people_list = self.get(f"/api/v1/people?q={employee_no}&employment_status=active")
        self.assertEqual(department_list["page"]["total"], 1)
        self.assertEqual(department_list["data"][0]["department_code"], root_code)
        self.assertEqual(people_list["page"]["total"], 1)
        self.assertEqual(people_list["data"][0]["primary_department_id"], child_id)

        _, root_detail = self.get(f"/api/v1/departments/{root_id}")
        _, person_detail = self.get(f"/api/v1/people/{person_id}")
        self.assertEqual(root_detail["data"]["children"][0]["department_id"], child_id)
        self.assertEqual(person_detail["data"]["department_code"], child_code)
        self.assertEqual(person_detail["data"]["agent_relationships"], [])

        _, renamed_body = self.request("PATCH", f"/api/v1/departments/{root_id}", {
            "row_version": root["row_version"],
            "name": "组织管理验证总部（改名）",
        })
        renamed = renamed_body["data"]
        self.assertEqual(renamed["department_code"], root_code)
        self.assertEqual(renamed["row_version"], root["row_version"] + 1)

        with self.assertRaises(HTTPError) as stale:
            self.request("PATCH", f"/api/v1/departments/{root_id}", {
                "row_version": root["row_version"],
                "name": "不应提交的过期修改",
            })
        self.assertEqual(stale.exception.code, 409)
        with self.assertRaises(HTTPError) as immutable_department:
            self.request("PATCH", f"/api/v1/departments/{root_id}", {
                "row_version": renamed["row_version"],
                "department_code": f"CHANGED-{root_code}",
            })
        self.assertEqual(immutable_department.exception.code, 422)
        with self.assertRaises(HTTPError) as immutable_person:
            self.request("PATCH", f"/api/v1/people/{person_id}", {
                "row_version": person["row_version"],
                "employee_no": f"CHANGED-{employee_no}",
            })
        self.assertEqual(immutable_person.exception.code, 422)

        with self.assertRaises(HTTPError) as active_child_conflict:
            self.request("PATCH", f"/api/v1/departments/{root_id}", {
                "row_version": renamed["row_version"],
                "is_active": False,
            })
        self.assertEqual(active_child_conflict.exception.code, 409)
        with self.assertRaises(HTTPError) as active_person_conflict:
            self.request("PATCH", f"/api/v1/departments/{child_id}", {
                "row_version": child["row_version"],
                "is_active": False,
            })
        self.assertEqual(active_person_conflict.exception.code, 409)

        with self.assertRaises(HTTPError) as hierarchy_cycle:
            self.request("PATCH", f"/api/v1/departments/{root_id}", {
                "row_version": renamed["row_version"],
                "parent_department_id": grandchild_id,
            })
        self.assertEqual(hierarchy_cycle.exception.code, 422)

        _, inactive_person_body = self.request("PATCH", f"/api/v1/people/{person_id}", {
            "row_version": person["row_version"],
            "employment_status": "inactive",
        })
        inactive_person = inactive_person_body["data"]
        self.assertEqual(inactive_person["employment_status"], "inactive")
        _, inactive_grandchild_body = self.request(
            "PATCH", f"/api/v1/departments/{grandchild_id}", {
                "row_version": grandchild["row_version"],
                "is_active": False,
            },
        )
        self.assertFalse(inactive_grandchild_body["data"]["is_active"])
        _, inactive_child_body = self.request("PATCH", f"/api/v1/departments/{child_id}", {
            "row_version": child["row_version"],
            "is_active": False,
        })
        inactive_child = inactive_child_body["data"]
        self.assertFalse(inactive_child["is_active"])
        _, inactive_root_body = self.request("PATCH", f"/api/v1/departments/{root_id}", {
            "row_version": renamed["row_version"],
            "is_active": False,
        })
        inactive_root = inactive_root_body["data"]
        self.assertFalse(inactive_root["is_active"])

        with self.assertRaises(HTTPError) as inactive_parent:
            self.request("PATCH", f"/api/v1/departments/{child_id}", {
                "row_version": inactive_child["row_version"],
                "is_active": True,
            })
        self.assertEqual(inactive_parent.exception.code, 422)
        with self.assertRaises(HTTPError) as inactive_person_department:
            self.request("PATCH", f"/api/v1/people/{person_id}", {
                "row_version": inactive_person["row_version"],
                "employment_status": "active",
            })
        self.assertEqual(inactive_person_department.exception.code, 422)
        with self.assertRaises(HTTPError) as inactive_person_agent_reference:
            self.request("POST", "/api/v1/agents", {
                "name": "不应绑定停用人员的智能体",
                "structure_role": "primary",
                "service_archetype": "business",
                "business_admin_person_id": person_id,
            })
        self.assertEqual(inactive_person_agent_reference.exception.code, 422)
        with self.assertRaises(HTTPError) as inactive_department_agent_reference:
            self.request("POST", "/api/v1/agents", {
                "name": "不应绑定停用部门的智能体",
                "structure_role": "primary",
                "service_archetype": "business",
                "owning_department_id": child_id,
            })
        self.assertEqual(inactive_department_agent_reference.exception.code, 422)

        _, restored_root_body = self.request("PATCH", f"/api/v1/departments/{root_id}", {
            "row_version": inactive_root["row_version"],
            "is_active": True,
        })
        restored_root = restored_root_body["data"]
        self.assertTrue(restored_root["is_active"])
        self.assertEqual(
            [entry["operation"] for entry in restored_root["change_log"][:4]],
            ["restore", "retire", "update", "create"],
        )

        with self.assertRaises(HTTPError) as no_department_delete:
            self.request("DELETE", f"/api/v1/departments/{root_id}")
        self.assertEqual(no_department_delete.exception.code, 405)
        with self.assertRaises(HTTPError) as no_person_delete:
            self.request("DELETE", f"/api/v1/people/{person_id}")
        self.assertEqual(no_person_delete.exception.code, 405)

        with self.assertRaises(HTTPError) as duplicate_department:
            self.request("POST", "/api/v1/departments", {
                "department_code": root_code,
                "name": "重复编码部门",
            })
        self.assertEqual(duplicate_department.exception.code, 409)
        with self.assertRaises(HTTPError) as duplicate_person:
            self.request("POST", "/api/v1/people", {
                "employee_no": employee_no,
                "display_name": "重复工号人员",
            })
        self.assertEqual(duplicate_person.exception.code, 409)

        independent_key = f"orguser{suffix}"
        _, independent_person_body = self.request("POST", "/api/v1/people", {
            "employee_no": independent_key,
            "display_name": "人员与登录用户独立验证",
        })
        _, independent_user_body = self.request("POST", "/api/v1/users", {
            "username": independent_key,
            "display_name": "同标识登录用户",
            "password": "Independent!Secure2026",
            "role": "viewer",
        })
        independent_person = independent_person_body["data"]
        independent_user = independent_user_body["data"]
        self.assertNotEqual(independent_person["person_id"], independent_user["user_id"])
        _, disabled_user_body = self.request(
            "PATCH", f"/api/v1/users/{independent_user['user_id']}", {
                "row_version": independent_user["row_version"],
                "status": "disabled",
            },
        )
        self.assertEqual(disabled_user_body["data"]["status"], "disabled")
        _, still_active_person = self.get(f"/api/v1/people/{independent_person['person_id']}")
        self.assertEqual(still_active_person["data"]["employment_status"], "active")

    def test_zzz_concurrent_department_moves_cannot_create_a_cycle(self):
        suffix = str(int(time.time() * 1000))
        _, first_body = self.request("POST", "/api/v1/departments", {
            "department_code": f"ORG-CONCURRENT-A-{suffix}",
            "name": "并发层级甲",
        })
        _, second_body = self.request("POST", "/api/v1/departments", {
            "department_code": f"ORG-CONCURRENT-B-{suffix}",
            "name": "并发层级乙",
        })
        first = first_body["data"]
        second = second_body["data"]
        barrier = threading.Barrier(2)
        results = []

        def move(department, parent):
            service = OrganizationManagementService(WriteDatabase(DATABASE_URL))
            barrier.wait(timeout=5)
            try:
                service.update_department(
                    department["department_id"],
                    {"parent_department_id": parent["department_id"]},
                    department["row_version"],
                    "integration-admin",
                )
                results.append("committed")
            except ValueError:
                results.append("cycle-rejected")

        threads = (
            threading.Thread(target=move, args=(first, second)),
            threading.Thread(target=move, args=(second, first)),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(results, ["committed", "cycle-rejected"])

        _, first_detail = self.get(f"/api/v1/departments/{first['department_id']}")
        _, second_detail = self.get(f"/api/v1/departments/{second['department_id']}")
        self.assertFalse(
            first_detail["data"]["parent_department_id"] == second["department_id"]
            and second_detail["data"]["parent_department_id"] == first["department_id"]
        )

    def test_zzzy_agent_keeps_historical_inactive_reference_but_cannot_add_one(self):
        suffix = str(int(time.time() * 1000))
        _, first_person_body = self.request("POST", "/api/v1/people", {
            "employee_no": f"HISTORY-A-{suffix}",
            "display_name": "历史管理员甲",
        })
        first_person = first_person_body["data"]
        _, agent_body = self.request("POST", "/api/v1/agents", {
            "name": "历史组织引用验证智能体",
            "structure_role": "primary",
            "service_archetype": "business",
            "lifecycle_status": "requested",
            "business_admin_person_id": first_person["person_id"],
            "business_domain": "组织引用测试",
            "business_scenario": "历史引用保留",
        })
        agent = agent_body["data"]
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "UPDATE org.person SET employment_status = 'inactive' WHERE person_id = %s",
                (first_person["person_id"],),
            )

        _, unrelated_update = self.request("PATCH", f"/api/v1/agents/{agent['agent_id']}", {
            "row_version": agent["row_version"],
            "description": "不改变既有组织引用",
        })
        updated_agent = unrelated_update["data"]
        self.assertEqual(updated_agent["business_admin_person_id"], first_person["person_id"])

        _, second_person_body = self.request("POST", "/api/v1/people", {
            "employee_no": f"HISTORY-B-{suffix}",
            "display_name": "历史管理员乙",
        })
        second_person = second_person_body["data"]
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "UPDATE org.person SET employment_status = 'inactive' WHERE person_id = %s",
                (second_person["person_id"],),
            )
        with self.assertRaises(HTTPError) as inactive_new_reference:
            self.request("PATCH", f"/api/v1/agents/{agent['agent_id']}", {
                "row_version": updated_agent["row_version"],
                "business_admin_person_id": second_person["person_id"],
            })
        self.assertEqual(inactive_new_reference.exception.code, 422)

    def test_invalid_pagination_is_rejected(self):
        with self.assertRaises(HTTPError) as invalid:
            self.get("/api/v1/servers?limit=101")
        self.assertEqual(invalid.exception.code, 422)

        with self.assertRaises(HTTPError) as invalid_status:
            self.get("/api/v1/servers?lifecycle_status=online")
        self.assertEqual(invalid_status.exception.code, 422)


if __name__ == "__main__":
    unittest.main()
