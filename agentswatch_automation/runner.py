import errno
import fcntl
import os
import re
import shlex
import shutil
import stat
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .catalog import OperationCatalog
from .paths import CODE_AUTOMATION_ROOT, artifact_root, runner_root
from .redaction import redact_sensitive_data, redact_sensitive_text


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION_ROOT = CODE_AUTOMATION_ROOT
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TRACKED_EVENTS = {
    "playbook_on_start", "playbook_on_play_start", "playbook_on_task_start",
    "runner_on_start", "runner_on_ok", "runner_on_failed",
    "runner_on_unreachable", "runner_on_skipped", "playbook_on_stats",
}
RUNNER_WORKSPACE_MARKER = ".agentswatch-runner-workspace"
RUNNER_WORKSPACE_LOCK = ".agentswatch-runner-lock"
RUNNER_GENERATED_ENV_ALLOWLIST = {
    "ANSIBLE_CALLBACK_PLUGINS",
    "ANSIBLE_STDOUT_CALLBACK",
    "ANSIBLE_RETRY_FILES_ENABLED",
    "ANSIBLE_CACHE_PLUGIN",
    "ANSIBLE_CACHE_PLUGIN_CONNECTION",
    "AWX_ISOLATED_DATA_DIR",
    "RUNNER_OMIT_EVENTS",
    "RUNNER_ONLY_FAILED_EVENTS",
}


def _secure_runner_root() -> Path:
    root = runner_root()
    if root.is_symlink():
        raise RuntimeError(f"runner workspace root must not be a symlink: {root}")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.chmod(0o700)
    if root.is_symlink() or not stat.S_ISDIR(root.lstat().st_mode):
        raise RuntimeError(f"runner workspace root is not a secure directory: {root}")
    return root


def cleanup_stale_runner_workspaces() -> int:
    """Remove only AgentsWatch workspaces whose owning runner has exited.

    Each live run holds an advisory lock for its entire lifetime. Unmarked
    directories are deliberately left alone because their ownership cannot be
    proven, and the persistent artifact library is outside this root.
    """
    root = _secure_runner_root()
    removed = 0
    for workspace in root.iterdir():
        if workspace.is_symlink() or not workspace.is_dir():
            continue
        marker = workspace / RUNNER_WORKSPACE_MARKER
        lock = workspace / RUNNER_WORKSPACE_LOCK
        if (
            marker.is_symlink() or lock.is_symlink()
            or not marker.is_file() or not lock.is_file()
        ):
            continue
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(lock, flags)
        except OSError:
            continue
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    continue
                raise
            shutil.rmtree(workspace, ignore_errors=False)
            removed += 1
        finally:
            os.close(lock_fd)
    return removed


def _create_runner_workspace(identifier: str) -> tuple[Path, int]:
    cleanup_stale_runner_workspaces()
    workspace = Path(tempfile.mkdtemp(prefix=f"{identifier}-", dir=_secure_runner_root()))
    lock_fd = None
    try:
        workspace.chmod(0o700)
        lock = workspace / RUNNER_WORKSPACE_LOCK
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        lock_fd = os.open(lock, flags, 0o600)
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Publish the ownership marker only after the lifetime lock is held, so
        # a concurrent cleanup can never mistake a workspace being created for
        # a terminated run.
        marker = workspace / RUNNER_WORKSPACE_MARKER
        marker.write_text(f"pid={os.getpid()}\n", encoding="utf-8")
        marker.chmod(0o600)
        return workspace, lock_fd
    except Exception:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        shutil.rmtree(workspace, ignore_errors=True)
        raise


def _safe_runner_environment(prepared: dict[str, str], runner_path: str) -> dict[str, str]:
    safe = {
        "PATH": runner_path,
        "HOME": os.environ.get("HOME") or str(Path.home()),
        "LANG": os.environ.get("LANG") or "C.UTF-8",
        "ANSIBLE_CONFIG": str(AUTOMATION_ROOT / "ansible.cfg"),
        "ANSIBLE_HOST_KEY_CHECKING": "True",
    }
    for name in RUNNER_GENERATED_ENV_ALLOWLIST:
        if name in prepared:
            safe[name] = prepared[name]
    if os.environ.get("SSH_AUTH_SOCK"):
        safe["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    return safe


class AutomationRunner:
    def __init__(self, catalog: OperationCatalog | None = None):
        self.catalog = catalog or OperationCatalog()

    @staticmethod
    def _event_snapshot(event: dict[str, Any]) -> dict[str, Any] | None:
        event_type = str(event.get("event") or "").strip()
        if event_type not in TRACKED_EVENTS:
            return None
        data = event.get("event_data") or {}
        result = data.get("res") or {}
        no_log = bool(result.get("_ansible_no_log"))
        stdout = redact_sensitive_text(ANSI_ESCAPE.sub("", str(event.get("stdout") or "")).strip())
        if no_log:
            stdout = "[任务输出已按 no_log 隐藏]"
        safe_data = {}
        for name in ("duration", "changed", "rc", "skip_reason"):
            value = result.get(name) if name in result else data.get(name)
            if value is not None and isinstance(value, (str, int, float, bool)):
                safe_data[name] = value
        message = result.get("msg")
        if message and not no_log:
            safe_data["message"] = redact_sensitive_text(str(message)[:2000])
        level = "error" if event_type in {"runner_on_failed", "runner_on_unreachable"} else "warning" if event_type == "runner_on_skipped" else "info"
        return {
            "phase": "ansible",
            "event_type": event_type,
            "event_level": level,
            "target_key": str(data.get("host") or "").strip() or None,
            "play_name": str(data.get("play") or "").strip() or None,
            "task_name": str(data.get("task") or "").strip() or None,
            "task_action": str(data.get("task_action") or "").strip() or None,
            "changed": result.get("changed") if isinstance(result.get("changed"), bool) else None,
            "stdout": stdout or None,
            "event_data": redact_sensitive_data(safe_data),
            "occurred_at": event.get("created"),
        }

    @staticmethod
    def _inventory(
        targets: list[dict[str, Any]], known_hosts_file: Path | None = None,
    ) -> dict[str, Any]:
        if not targets:
            raise ValueError("at least one target is required")
        hosts = {}
        for target in targets:
            target_id = str(target.get("server_id") or "").strip()
            if not target_id:
                raise ValueError("each target requires server_id")
            if target_id in hosts:
                raise ValueError(f"duplicate server target: {target_id}")
            address = str(target.get("address") or "").strip()
            if not address:
                raise ValueError(f"target {target_id} requires address")
            variables = {"ansible_host": address}
            for source, destination in (
                ("connection", "ansible_connection"),
                ("port", "ansible_port"),
                ("user", "ansible_user"),
                ("python_interpreter", "ansible_python_interpreter"),
                ("private_key_file", "ansible_ssh_private_key_file"),
                ("password", "ansible_password"),
                ("become_password", "ansible_become_password"),
                ("become", "ansible_become"),
            ):
                if target.get(source) is not None:
                    variables[destination] = target[source]
            if target.get("ssh_host_keys") and known_hosts_file is not None:
                variables["ansible_ssh_common_args"] = (
                    f"-o UserKnownHostsFile={shlex.quote(str(known_hosts_file))} "
                    "-o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=yes"
                )
            for name in (
                "runtime_management_driver", "runtime_management_target",
                "runtime_endpoint_port", "runtime_probe_host",
                "runtime_family_code", "runtime_config_path", "runtime_state_path",
                "runtime_command_path",
                "agentswatch_agent_definitions", "agentswatch_retired_agent_definitions",
                "agentswatch_channel_patch_file",
                "agentswatch_require_existing_agent_keys",
                "agentswatch_allow_existing_agent_keys",
                "agentswatch_require_owned_agent_keys",
                "agentswatch_expected_channel_enabled",
                "agentswatch_ownership_cleanup_mode",
                "agentswatch_bootstrap_public_key",
            ):
                if target.get(name) is not None:
                    variables[name] = target[name]
            hosts[target_id] = variables
        return {"all": {"hosts": hosts}}

    @staticmethod
    def _write_known_hosts(targets: list[dict[str, Any]], workspace: Path) -> Path | None:
        lines = []
        for target in targets:
            for line in target.get("ssh_host_keys") or []:
                value = str(line).strip()
                if value and value not in lines:
                    lines.append(value)
        if not lines:
            return None
        path = workspace / "ssh_known_hosts"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    def run(
        self,
        operation_code: str,
        targets: list[dict[str, Any]],
        parameters: dict[str, Any] | None = None,
        run_id: str | None = None,
        event_callback=None,
    ) -> dict[str, Any]:
        operation = self.catalog.get(operation_code)
        validated_parameters = self.catalog.validate_parameters(operation_code, parameters)

        try:
            from ansible_runner.interface import init_runner
        except ImportError as exc:
            raise RuntimeError("ansible-runner is not installed; install requirements-automation.txt") from exc

        identifier = run_id or str(uuid.uuid4())
        host_results: dict[str, dict[str, Any]] = {
            str(target["server_id"]): {"status": "pending", "result": None, "error": None}
            for target in targets
        }
        transient_secrets = sorted({
            str(target[name])
            for target in targets
            for name in ("password", "become_password")
            if target.get(name)
        }, key=len, reverse=True)

        def redact_run_text(value):
            safe = redact_sensitive_text(value)
            if safe is None:
                return None
            for secret in transient_secrets:
                safe = safe.replace(secret, "[REDACTED]")
            return safe

        def replace_transient_secrets(value):
            if isinstance(value, dict):
                return {
                    key: replace_transient_secrets(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [replace_transient_secrets(item) for item in value]
            if isinstance(value, str):
                safe = value
                for secret in transient_secrets:
                    safe = safe.replace(secret, "[REDACTED]")
                return safe
            return value

        def redact_run_data(value):
            return replace_transient_secrets(redact_sensitive_data(value))

        def consume(event: dict[str, Any]):
            event_name = event.get("event")
            event_data = event.get("event_data") or {}
            host = event_data.get("host")
            if host in host_results:
                result = event_data.get("res") or {}
                if event_name == "runner_on_unreachable":
                    host_results[host].update(status="unreachable", error=redact_run_text(result.get("msg")))
                elif event_name == "runner_on_failed":
                    host_results[host].update(status="failed", error=redact_run_text(result.get("msg")))
                elif event_name == "runner_on_ok":
                    normalized = result.get("agentswatch_result")
                    if normalized is None:
                        normalized = (result.get("ansible_facts") or {}).get("agentswatch_result")
                    if normalized is not None:
                        host_results[host].update(status="succeeded", result=redact_run_data(normalized))
            snapshot = self._event_snapshot(event)
            if snapshot is not None and event_callback is not None:
                event_callback(redact_run_data(snapshot))
            return True

        # Keep the virtualenv path itself; resolving the Python symlink would
        # point at the base interpreter and hide the venv's ansible-playbook.
        executable_dir = str(Path(sys.executable).absolute().parent)
        runner_path = os.environ.get("PATH", "")
        if executable_dir not in runner_path.split(os.pathsep):
            runner_path = executable_dir + os.pathsep + runner_path

        resolved_playbook = operation.playbook.resolve()
        try:
            playbook_argument = str(resolved_playbook.relative_to(AUTOMATION_ROOT.resolve()))
        except ValueError:
            playbook_argument = str(resolved_playbook)
        private_data_dir, workspace_lock_fd = _create_runner_workspace(identifier)
        try:
            if any(
                target.get("password") and target.get("connection") != "local"
                for target in targets
            ) and not shutil.which("sshpass"):
                raise RuntimeError("sshpass is required on the management server for password bootstrap")
            known_hosts_file = self._write_known_hosts(targets, private_data_dir)
            result = init_runner(
                private_data_dir=str(private_data_dir),
                project_dir=str(AUTOMATION_ROOT),
                playbook=playbook_argument,
                inventory=self._inventory(targets, known_hosts_file),
                extravars=validated_parameters,
                envvars={
                    "ANSIBLE_CONFIG": str(AUTOMATION_ROOT / "ansible.cfg"),
                    "ANSIBLE_HOST_KEY_CHECKING": "True",
                    "PATH": runner_path,
                },
                suppress_env_files=True,
                ident=identifier,
                timeout=operation.timeout_seconds,
                quiet=True,
                event_handler=consume,
            )
            # RunnerConfig starts from os.environ. Replace it after preparation
            # so API database DSNs, encryption keys and unrelated process
            # secrets can neither reach Ansible nor its `command` artifact.
            result.config.env = _safe_runner_environment(result.config.env, runner_path)
            result.run()

            stats = result.stats or {}
            for host, host_result in host_results.items():
                if host in (stats.get("dark") or {}):
                    host_result["status"] = "unreachable"
                elif host in (stats.get("failures") or {}):
                    host_result["status"] = "failed"
                elif host in (stats.get("processed") or {}) or host in (stats.get("ok") or {}):
                    host_result["status"] = "succeeded"
                elif host_result["status"] == "pending":
                    if result.rc == 0 and result.status == "successful":
                        host_result["status"] = "succeeded"
                    else:
                        host_result.update(status="failed", error="Ansible did not return a terminal host result")

            # Retain only the human-readable stdout needed by local tests. Runner command,
            # environment and inventory artifacts can contain credentials and are deleted.
            safe_artifact_dir = artifact_root() / identifier
            safe_artifact_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            safe_artifact_dir.chmod(0o700)
            source_stdout = Path(result.config.artifact_dir) / "stdout"
            if source_stdout.is_file():
                safe_stdout = redact_run_text(source_stdout.read_text(encoding="utf-8", errors="replace")) or ""
                (safe_artifact_dir / "stdout").write_text(safe_stdout, encoding="utf-8")
            else:
                (safe_artifact_dir / "stdout").write_text("", encoding="utf-8")
            (safe_artifact_dir / "stdout").chmod(0o600)
            return {
                "run_id": identifier,
                "operation_code": operation.code,
                "status": result.status,
                "return_code": result.rc,
                "hosts": host_results,
                "artifact_dir": str(safe_artifact_dir),
            }
        finally:
            fcntl.flock(workspace_lock_fd, fcntl.LOCK_UN)
            os.close(workspace_lock_fd)
            shutil.rmtree(private_data_dir, ignore_errors=True)
