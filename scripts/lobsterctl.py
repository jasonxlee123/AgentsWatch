#!/usr/bin/env python3
import argparse
import getpass
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


DEFAULT_INVENTORY = Path("cluster/lobsters.json")
DEFAULT_SCRAPE_CONFIGS = "prometheus/scrape_configs/openclaw-gateways.yml"
DEFAULT_SECRETS_DIR = Path("prometheus/secrets/gateways")
DEFAULT_ENV_FILE = Path(".env")
DEFAULT_OPENCLAW_CONFIG_PATH = "/home/openclaw/.openclaw/openclaw.json"
GATEWAY_TOKEN_DIR_MODE = 0o2750
GATEWAY_TOKEN_FILE_MODE = 0o640
READ_GATEWAY_TOKEN_JS = (
    'const fs=require("node:fs");'
    'const file=process.argv[1];'
    'const raw=fs.readFileSync(file,"utf8");'
    'let config;'
    'try{config=JSON.parse(raw);}'
    'catch(jsonError){try{'
    'const childProcess=require("node:child_process");'
    'const {createRequire}=require("node:module");'
    'const executable=process.env.OPENCLAW_BIN||childProcess.execFileSync('
    '"sh",["-c","command -v openclaw"],{encoding:"utf8"}).trim();'
    'config=createRequire(fs.realpathSync(executable))("json5").parse(raw);'
    '}catch(error){console.error(`unable to parse ${file}: ${error.message}`);process.exit(2);}}'
    'const token=config?.gateway?.auth?.token;'
    'if(typeof token!=="string"||!token.trim()){'
    'console.error("gateway.auth.token is missing or is not a literal string");process.exit(3);}'
    'process.stdout.write(token.trim());'
)


def load_inventory(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"inventory not found: {path}")
    data = json.loads(path.read_text())
    data.setdefault("defaults", {})
    data.setdefault("gateways", [])
    return data


def save_inventory(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def find_gateway(data: dict, gateway_id: str) -> tuple[int, dict] | tuple[None, None]:
    for idx, gateway in enumerate(data.get("gateways", [])):
        if gateway.get("gateway_id") == gateway_id:
            return idx, gateway
    return None, None


def require_gateway(data: dict, gateway_id: str) -> dict:
    _, gateway = find_gateway(data, gateway_id)
    if gateway is None:
        raise ValueError(f"gateway not found: {gateway_id}")
    return gateway


def gateway_token_path(secrets_dir: Path, gateway_id: str) -> Path:
    return secrets_dir / f"{gateway_id}.token"


def gateway_token_valid(path: Path) -> bool:
    return (
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_size > 0
        and stat.S_IMODE(path.stat().st_mode) == GATEWAY_TOKEN_FILE_MODE
    )


def write_gateway_token(path: Path, token: str) -> None:
    token = token.strip()
    if not token:
        raise ValueError("gateway token must not be empty")
    path.parent.mkdir(parents=True, exist_ok=True, mode=GATEWAY_TOKEN_DIR_MODE)
    os.chmod(path.parent, GATEWAY_TOKEN_DIR_MODE)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, GATEWAY_TOKEN_FILE_MODE)
        with os.fdopen(fd, "w") as handle:
            handle.write(token)
        os.replace(temporary_name, path)
        os.chmod(path, GATEWAY_TOKEN_FILE_MODE)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temporary_name).unlink(missing_ok=True)
        raise


def reload_prometheus(base_url: str) -> None:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/-/reload", method="POST")
    with urllib.request.urlopen(request, timeout=15):
        pass
    print(f"reloaded Prometheus at {base_url}")


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        values[name] = value
    return values


def run_ssh_command(command: list[str]) -> subprocess.CompletedProcess:
    allowed_environment = {
        "HOME",
        "LANG",
        "LOGNAME",
        "PATH",
        "SHELL",
        "SSH_AUTH_SOCK",
        "TMPDIR",
        "USER",
    }
    env = {
        name: value
        for name, value in os.environ.items()
        if name in allowed_environment or name.startswith("LC_")
    }
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=env,
    )


def require_readable_file(path: Path, label: str) -> Path:
    path = path.expanduser()
    if not path.is_file() or not os.access(path, os.R_OK) or path.stat().st_size == 0:
        raise ValueError(f"{label} must be a non-empty readable regular file: {path}")
    return path


def command_render(args: argparse.Namespace) -> int:
    if args.reload and command_token_status(args) != 0:
        raise ValueError("refusing to reload Prometheus while enabled gateways have missing or insecure token files")
    cmd = [
        sys.executable,
        "scripts/generate_lobster_configs.py",
        "--inventory",
        str(args.inventory),
        "--scrape-configs",
        args.scrape_configs,
    ]
    if args.otel_endpoint:
        cmd.extend(["--otel-endpoint", args.otel_endpoint])
    subprocess.run(cmd, check=True)
    if args.reload:
        reload_prometheus(args.prometheus_url)
    return 0


def command_list(args: argparse.Namespace) -> int:
    data = load_inventory(args.inventory)
    rows = []
    for gateway in data.get("gateways", []):
        rows.append(
            [
                gateway.get("gateway_id", ""),
                "yes" if gateway.get("enabled", True) else "no",
                gateway.get("host", ""),
                str(gateway.get("port", data.get("defaults", {}).get("port", 16402))),
                gateway.get("deployment_mode", ""),
                gateway.get("lobster_name", ""),
                gateway.get("department", ""),
            ]
        )
    widths = [max(len(row[idx]) for row in ([["gateway_id", "enabled", "host", "port", "mode", "name", "department"]] + rows)) for idx in range(7)]
    header = ["gateway_id", "enabled", "host", "port", "mode", "name", "department"]
    print("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(header)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))
    return 0


def command_add(args: argparse.Namespace) -> int:
    data = load_inventory(args.inventory)
    _, existing = find_gateway(data, args.gateway_id)
    if existing and not args.update:
        raise ValueError(f"gateway already exists: {args.gateway_id}. Use --update to modify it.")
    item = existing if existing is not None else {"gateway_id": args.gateway_id}
    item.update(
        {
            "host": args.host,
            "port": args.port,
            "enabled": True,
            "deployment_mode": args.deployment_mode,
        }
    )
    for key in (
        "cluster",
        "env",
        "region",
        "team",
        "scheme",
        "metrics_path",
        "otel_endpoint",
        "lobster_name",
        "department",
        "service_mode",
        "container_name",
        "ssh_user",
        "openclaw_user",
        "openclaw_config_path",
        "container_user",
    ):
        value = getattr(args, key)
        if value:
            item[key] = value
    if args.ssh_port is not None:
        item["ssh_port"] = args.ssh_port
    if args.sample_rate is not None:
        item["sample_rate"] = args.sample_rate
    if args.flush_interval_ms is not None:
        item["flush_interval_ms"] = args.flush_interval_ms
    if existing is None:
        data["gateways"].append(item)
    save_inventory(args.inventory, data)
    if args.render:
        command_render(args)
    return 0


def set_enabled(args: argparse.Namespace, value: bool) -> int:
    data = load_inventory(args.inventory)
    _, gateway = find_gateway(data, args.gateway_id)
    if gateway is None:
        raise ValueError(f"gateway not found: {args.gateway_id}")
    if value and not gateway_token_valid(gateway_token_path(args.secrets_dir, args.gateway_id)):
        raise ValueError(f"cannot enable {args.gateway_id}: individual token file is missing or insecure")
    gateway["enabled"] = value
    save_inventory(args.inventory, data)
    if args.render:
        command_render(args)
    return 0


def command_delete(args: argparse.Namespace) -> int:
    data = load_inventory(args.inventory)
    idx, gateway = find_gateway(data, args.gateway_id)
    if gateway is None:
        raise ValueError(f"gateway not found: {args.gateway_id}")
    del data["gateways"][idx]
    save_inventory(args.inventory, data)
    token_path = gateway_token_path(args.secrets_dir, args.gateway_id)
    if token_path.exists():
        token_path.unlink()
        print(f"deleted monitoring token copy {token_path}")
    if args.render:
        command_render(args)
    return 0


def command_token_set(args: argparse.Namespace) -> int:
    data = load_inventory(args.inventory)
    require_gateway(data, args.gateway_id)
    if args.from_file:
        token = args.from_file.read_text().strip()
    else:
        token = getpass.getpass(f"Existing Gateway token for {args.gateway_id}: ")
    path = gateway_token_path(args.secrets_dir, args.gateway_id)
    write_gateway_token(path, token)
    print(f"stored existing Gateway token in {path} with mode 0640")
    return 0


def command_token_status(args: argparse.Namespace) -> int:
    data = load_inventory(args.inventory)
    invalid = []
    for gateway in data.get("gateways", []):
        gateway_id = gateway.get("gateway_id", "")
        enabled = gateway.get("enabled", True) is not False
        path = gateway_token_path(args.secrets_dir, gateway_id)
        present = path.is_file() and not path.is_symlink() and path.stat().st_size > 0
        mode = stat.S_IMODE(path.stat().st_mode) if present else None
        secure = gateway_token_valid(path)
        if not present:
            state = "missing"
        elif not secure:
            state = f"insecure:{mode:04o}"
        else:
            state = "present"
        print(f"{gateway_id}\t{'enabled' if enabled else 'disabled'}\t{state}")
        if enabled and (not present or not secure):
            invalid.append(gateway_id)
    if invalid:
        print(f"missing or insecure token files for {len(invalid)} enabled gateways", file=sys.stderr)
        return 1
    print("all enabled gateways have an individual token file")
    return 0


def command_token_collect(args: argparse.Namespace) -> int:
    data = load_inventory(args.inventory)
    args.secrets_dir.mkdir(parents=True, exist_ok=True, mode=GATEWAY_TOKEN_DIR_MODE)
    os.chmod(args.secrets_dir, GATEWAY_TOKEN_DIR_MODE)
    collected = 0
    skipped = 0
    for gateway in data.get("gateways", []):
        if gateway.get("enabled", True) is False:
            continue
        gateway_id = gateway["gateway_id"]
        path = gateway_token_path(args.secrets_dir, gateway_id)
        if not args.overwrite and gateway_token_valid(path):
            skipped += 1
            continue
        name = gateway.get("lobster_name", "")
        host = gateway.get("host", "")
        print(f"collecting {gateway_id} | {name} | {host}")
        token = getpass.getpass("Existing Gateway token: ")
        write_gateway_token(path, token)
        collected += 1
    print(f"stored {collected} token files; skipped {skipped} existing files")
    return 0


def remote_token_command(
    gateway: dict,
    args: argparse.Namespace,
    default_config_path: str = DEFAULT_OPENCLAW_CONFIG_PATH,
) -> str:
    config_path = gateway.get("openclaw_config_path") or default_config_path
    node_arguments = shlex.join(["-e", READ_GATEWAY_TOKEN_JS, config_path])
    resolver = (
        'node_bin="$(command -v node 2>/dev/null || true)";'
        'if [ -z "$node_bin" ];then for candidate in '
        '/usr/local/bin/node /usr/bin/node /opt/homebrew/bin/node '
        '"$HOME"/.local/bin/node "$HOME"/.volta/bin/node "$HOME"/.asdf/shims/node '
        '"$HOME"/.local/share/mise/shims/node "$HOME"/.nvm/versions/node/*/bin/node;'
        'do if [ -x "$candidate" ];then node_bin="$candidate";break;fi;done;fi;'
        'if [ -z "$node_bin" ];then echo "unable to locate node executable" >&2;exit 127;fi;'
        'openclaw_bin="$(command -v openclaw 2>/dev/null || true)";'
        'if [ -z "$openclaw_bin" ];then for candidate in '
        '/usr/local/bin/openclaw /usr/bin/openclaw /opt/homebrew/bin/openclaw '
        '"$HOME"/.local/bin/openclaw "$HOME"/.volta/bin/openclaw "$HOME"/.asdf/shims/openclaw '
        '"$HOME"/.local/share/mise/shims/openclaw "$HOME"/.nvm/versions/node/*/bin/openclaw;'
        'do if [ -x "$candidate" ];then openclaw_bin="$candidate";break;fi;done;fi;'
        'if [ -n "$openclaw_bin" ];then export OPENCLAW_BIN="$openclaw_bin";fi;'
        f'exec "$node_bin" {node_arguments}'
    )
    login_command = [
        "sh",
        "-c",
        resolver,
    ]
    if gateway.get("deployment_mode", "baremetal") == "container":
        container_name = gateway.get("container_name")
        if not container_name:
            raise ValueError(f"gateway {gateway['gateway_id']} is missing container_name")
        container_command = ["docker", "exec"]
        container_user = gateway.get("container_user") or args.container_user
        if container_user:
            container_command.extend(["--user", container_user])
        command = [*container_command, container_name, *login_command]
    else:
        command = login_command
        openclaw_user = gateway.get("openclaw_user") or args.openclaw_user
        if openclaw_user:
            command = ["sudo", "-n", "-H", "-iu", openclaw_user, "--", *command]
    return shlex.join(command)


def command_token_fetch(args: argparse.Namespace) -> int:
    data = load_inventory(args.inventory)
    env_values = read_env_file(args.env_file)
    default_ssh_user = args.ssh_user or os.environ.get("LOBSTER_SSH_USER") or env_values.get("LOBSTER_SSH_USER") or "openclaw"
    default_ssh_port = args.ssh_port or int(os.environ.get("LOBSTER_SSH_PORT") or env_values.get("LOBSTER_SSH_PORT") or 22)
    connect_timeout = args.connect_timeout or int(
        os.environ.get("LOBSTER_SSH_CONNECT_TIMEOUT")
        or env_values.get("LOBSTER_SSH_CONNECT_TIMEOUT")
        or 10
    )
    default_config_path = (
        getattr(args, "openclaw_config_path", None)
        or os.environ.get("LOBSTER_OPENCLAW_CONFIG_PATH")
        or env_values.get("LOBSTER_OPENCLAW_CONFIG_PATH")
        or DEFAULT_OPENCLAW_CONFIG_PATH
    )
    known_hosts_value = (
        getattr(args, "known_hosts", None)
        or os.environ.get("LOBSTER_SSH_KNOWN_HOSTS")
        or env_values.get("LOBSTER_SSH_KNOWN_HOSTS")
    )
    if not known_hosts_value:
        raise ValueError(
            "token-fetch requires --known-hosts or LOBSTER_SSH_KNOWN_HOSTS; "
            "the file must contain administrator-verified host keys"
        )
    known_hosts = require_readable_file(Path(known_hosts_value), "known_hosts file")
    identity_value = (
        getattr(args, "identity_file", None)
        or os.environ.get("LOBSTER_SSH_IDENTITY_FILE")
        or env_values.get("LOBSTER_SSH_IDENTITY_FILE")
    )
    identity_file = (
        require_readable_file(Path(identity_value), "identity file")
        if identity_value
        else None
    )
    ssh_agent_socket = os.environ.get("SSH_AUTH_SOCK", "") if identity_file is None else ""
    if identity_file is None and not ssh_agent_socket:
        raise ValueError(
            "token-fetch requires --identity-file/LOBSTER_SSH_IDENTITY_FILE "
            "or an active SSH agent (SSH_AUTH_SOCK)"
        )
    if args.gateway_id:
        gateways = [require_gateway(data, args.gateway_id)]
    else:
        gateways = [gateway for gateway in data.get("gateways", []) if gateway.get("enabled", True) is not False]

    fetched = 0
    planned = 0
    skipped = 0
    failures = []
    for gateway in gateways:
        gateway_id = gateway["gateway_id"]
        path = gateway_token_path(args.secrets_dir, gateway_id)
        if not args.dry_run and not args.overwrite and gateway_token_valid(path):
            skipped += 1
            continue
        ssh_user = gateway.get("ssh_user") or default_ssh_user
        ssh_port = int(gateway.get("ssh_port", default_ssh_port))
        ssh_target = f"{ssh_user}@{gateway['host']}" if ssh_user else gateway["host"]
        try:
            remote_command = remote_token_command(gateway, args, default_config_path)
        except ValueError as exc:
            failures.append((gateway_id, str(exc)))
            continue
        command = [
            "ssh",
            "-o",
            f"ConnectTimeout={connect_timeout}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "UpdateHostKeys=no",
            "-o",
            "BatchMode=yes",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "PubkeyAuthentication=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "ChallengeResponseAuthentication=no",
            "-o",
            "NumberOfPasswordPrompts=0",
            "-p",
            str(ssh_port),
        ]
        if identity_file is not None:
            command.extend(["-o", "IdentitiesOnly=yes", "-i", str(identity_file)])
        else:
            command.extend(
                [
                    "-o",
                    f"IdentityAgent={ssh_agent_socket}",
                    "-o",
                    "IdentityFile=none",
                ]
            )
        command.extend([ssh_target, remote_command])
        if args.dry_run:
            print(f"{gateway_id}\t{shlex.join(command)}")
            planned += 1
            continue
        print(f"fetching {gateway_id} | {gateway.get('lobster_name', '')} | {ssh_target}")
        result = run_ssh_command(command)
        if result.returncode != 0:
            message = result.stderr.strip() or f"ssh exited with status {result.returncode}"
            failures.append((gateway_id, message))
            continue
        token = result.stdout.strip()
        if not token or "\n" in token or token.startswith(("{", "[")):
            failures.append((gateway_id, "remote config did not return one plain, non-empty token"))
            continue
        write_gateway_token(path, token)
        fetched += 1
        print(f"stored token copy for {gateway_id}")

    if args.dry_run:
        for gateway_id, message in failures:
            print(f"FAIL {gateway_id}: {message}", file=sys.stderr)
        print(f"planned {planned} read-only SSH fetches; invalid {len(failures)}")
        return 1 if failures else 0
    for gateway_id, message in failures:
        print(f"FAIL {gateway_id}: {message}", file=sys.stderr)
    print(f"fetched {fetched} token files; skipped {skipped} existing files; failed {len(failures)}")
    return 1 if failures else 0


def command_token_delete(args: argparse.Namespace) -> int:
    data = load_inventory(args.inventory)
    require_gateway(data, args.gateway_id)
    path = gateway_token_path(args.secrets_dir, args.gateway_id)
    if path.exists():
        path.unlink()
        print(f"deleted monitoring token copy {path}")
    else:
        print(f"monitoring token copy does not exist: {path}")
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--scrape-configs", default=DEFAULT_SCRAPE_CONFIGS)
    parser.add_argument("--secrets-dir", type=Path, default=DEFAULT_SECRETS_DIR)
    parser.add_argument("--otel-endpoint", help="Override diagnostics OTLP HTTP base endpoint during render.")
    parser.add_argument("--reload", action="store_true", help="Reload Prometheus after rendering.")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")


def add_token_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--secrets-dir", type=Path, default=DEFAULT_SECRETS_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage lobster gateway inventory and generated monitoring config.")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List gateways in inventory")
    add_common(list_parser)
    list_parser.set_defaults(func=command_list)

    render_parser = sub.add_parser("render", help="Regenerate per-gateway Prometheus scrape configs and OpenClaw patches")
    add_common(render_parser)
    render_parser.set_defaults(func=command_render)

    add_parser = sub.add_parser("add", help="Add or update a gateway")
    add_common(add_parser)
    add_parser.add_argument("--gateway-id", required=True)
    add_parser.add_argument("--host", required=True)
    add_parser.add_argument("--port", type=int, default=16402)
    add_parser.add_argument("--deployment-mode", choices=("baremetal", "container"), default="baremetal")
    add_parser.add_argument("--container-name")
    add_parser.add_argument("--ssh-user")
    add_parser.add_argument("--ssh-port", type=int)
    add_parser.add_argument("--openclaw-user")
    add_parser.add_argument("--openclaw-config-path")
    add_parser.add_argument("--container-user")
    add_parser.add_argument("--cluster")
    add_parser.add_argument("--env")
    add_parser.add_argument("--region")
    add_parser.add_argument("--team")
    add_parser.add_argument("--scheme")
    add_parser.add_argument("--metrics-path")
    add_parser.add_argument("--lobster-name")
    add_parser.add_argument("--department")
    add_parser.add_argument("--service-mode")
    add_parser.add_argument("--sample-rate", type=float)
    add_parser.add_argument("--flush-interval-ms", type=int)
    add_parser.add_argument("--update", action="store_true")
    add_parser.add_argument("--render", action="store_true")
    add_parser.set_defaults(func=command_add)

    disable_parser = sub.add_parser("disable", help="Disable a gateway without deleting history metadata")
    add_common(disable_parser)
    disable_parser.add_argument("gateway_id")
    disable_parser.add_argument("--render", action="store_true")
    disable_parser.set_defaults(func=lambda args: set_enabled(args, False))

    enable_parser = sub.add_parser("enable", help="Enable a disabled gateway")
    add_common(enable_parser)
    enable_parser.add_argument("gateway_id")
    enable_parser.add_argument("--render", action="store_true")
    enable_parser.set_defaults(func=lambda args: set_enabled(args, True))

    delete_parser = sub.add_parser("delete", help="Hard delete a gateway from inventory")
    add_common(delete_parser)
    delete_parser.add_argument("gateway_id")
    delete_parser.add_argument("--render", action="store_true")
    delete_parser.set_defaults(func=command_delete)

    token_set_parser = sub.add_parser("token-set", help="Store an existing Gateway token in an individual Prometheus secret file")
    add_token_common(token_set_parser)
    token_set_parser.add_argument("gateway_id")
    token_set_parser.add_argument("--from-file", type=Path, help="Read the existing token from a local file instead of prompting.")
    token_set_parser.set_defaults(func=command_token_set)

    token_collect_parser = sub.add_parser("token-collect", help="Interactively collect missing existing Gateway tokens in inventory order")
    add_token_common(token_collect_parser)
    token_collect_parser.add_argument("--overwrite", action="store_true", help="Prompt for and replace token copies that already exist.")
    token_collect_parser.set_defaults(func=command_token_collect)

    token_fetch_parser = sub.add_parser("token-fetch", help="Fetch existing Gateway tokens over SSH without changing remote configuration")
    add_token_common(token_fetch_parser)
    token_fetch_parser.add_argument("gateway_id", nargs="?", help="Fetch one gateway; defaults to all enabled gateways.")
    token_fetch_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    token_fetch_parser.add_argument(
        "--known-hosts",
        type=Path,
        help="Required pinned known_hosts file; defaults to LOBSTER_SSH_KNOWN_HOSTS.",
    )
    token_fetch_parser.add_argument(
        "--identity-file",
        type=Path,
        help="Private key file; defaults to LOBSTER_SSH_IDENTITY_FILE. Omit to use an SSH agent.",
    )
    token_fetch_parser.add_argument("--ssh-user", help="Defaults to LOBSTER_SSH_USER or openclaw.")
    token_fetch_parser.add_argument("--ssh-port", type=int, help="Defaults to LOBSTER_SSH_PORT or 22.")
    token_fetch_parser.add_argument("--openclaw-user", help="Read the OpenClaw config as this remote Unix user.")
    token_fetch_parser.add_argument(
        "--openclaw-config-path",
        help=f"Defaults to LOBSTER_OPENCLAW_CONFIG_PATH or {DEFAULT_OPENCLAW_CONFIG_PATH}.",
    )
    token_fetch_parser.add_argument("--container-user", help="Run the read-only OpenClaw config command as this container user.")
    token_fetch_parser.add_argument("--connect-timeout", type=int, help="Defaults to LOBSTER_SSH_CONNECT_TIMEOUT or 10 seconds.")
    token_fetch_parser.add_argument("--overwrite", action="store_true", help="Replace monitoring copies that already exist.")
    token_fetch_parser.add_argument("--dry-run", action="store_true", help="Print read-only SSH commands without executing them.")
    token_fetch_parser.set_defaults(func=command_token_fetch)

    token_status_parser = sub.add_parser("token-status", help="Check individual token files for all managed gateways")
    add_token_common(token_status_parser)
    token_status_parser.set_defaults(func=command_token_status)

    token_delete_parser = sub.add_parser("token-delete", help="Delete only the monitoring server copy of a Gateway token")
    add_token_common(token_delete_parser)
    token_delete_parser.add_argument("gateway_id")
    token_delete_parser.set_defaults(func=command_token_delete)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
