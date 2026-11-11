#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid


NATIVE_CORE_METRICS = {
    "openclaw_model_call_total",
    "openclaw_run_completed_total",
    "openclaw_harness_run_total",
    "openclaw_session_state_total",
}

NATIVE_USAGE_METRICS = {
    "openclaw_model_tokens_total",
    "openclaw_model_cost_usd_total",
    "openclaw_model_usage_duration_seconds",
    "openclaw_gen_ai_client_token_usage",
}

OTEL_CORE_METRICS = {
    "target_info",
    "openclaw_telemetry_exporter_events_total",
}

OTEL_USAGE_METRICS = {
    "openclaw_tokens_total",
    "openclaw_cost_usd_total",
    "openclaw_model_call_duration_ms_milliseconds",
    "openclaw_session_state_total",
    "openclaw_session_turn_created_total",
    "gen_ai_client_token_usage",
}

IDENTITY_LABELS = {
    "gateway_id",
    "runtime_id",
    "server_id",
    "primary_agent_id",
    "agent_deployment_id",
}

LOKI_ACCEPTANCE_SERVICE = "agentswatch-telemetry-acceptance"


def read_token(path: str | None) -> str | None:
    if not path:
        return None
    with open(path) as handle:
        token = handle.read().strip()
    return token or None


def fetch_text(url: str, token: str | None = None) -> str:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str, headers: dict[str, str] | None = None, timeout: float = 10) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def post_json(url: str, payload: dict, headers: dict[str, str] | None = None, timeout: float = 10) -> int:
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()
        return response.status


def metric_names_from_text(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        if line.startswith("# HELP ") or line.startswith("# TYPE "):
            parts = line.split()
            if len(parts) >= 3:
                names.add(parts[2])
            continue
        match = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)", line)
        if match:
            names.add(match.group(1))
    return names


def prometheus_names(
    base_url: str,
    gateway_id: str | None,
    job: str | None = None,
    lookback_seconds: int | None = None,
) -> set[str]:
    matchers = []
    if job:
        matchers.append(f'job="{job}"')
    if gateway_id:
        matchers.append(f'gateway_id="{gateway_id}"')
    if matchers:
        matcher = "{" + ",".join(matchers) + "}"
        params: dict[str, str] = {"match[]": matcher}
        if lookback_seconds:
            params["start"] = str(int(time.time()) - lookback_seconds)
        query = urllib.parse.urlencode(params)
        url = urllib.parse.urljoin(base_url.rstrip("/") + "/", f"api/v1/series?{query}")
    else:
        url = urllib.parse.urljoin(base_url.rstrip("/") + "/", "api/v1/label/__name__/values")
    payload = fetch_json(url)
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")
    if matchers:
        names = {series["__name__"] for series in payload.get("data") or [] if "__name__" in series}
    else:
        names = set(payload.get("data") or [])
    expanded = set(names)
    for name in names:
        for suffix in ("_bucket", "_count", "_sum"):
            if name.endswith(suffix):
                expanded.add(name[: -len(suffix)])
    return expanded


def prometheus_query(base_url: str, expression: str) -> list[dict]:
    query = urllib.parse.urlencode({"query": expression})
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", f"api/v1/query?{query}")
    payload = fetch_json(url)
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")
    return (payload.get("data") or {}).get("result") or []


def target_status(result: list[dict]) -> dict:
    samples = []
    for item in result:
        sample = item.get("value") or []
        if len(sample) < 2:
            continue
        samples.append(
            {
                "instance": (item.get("metric") or {}).get("instance"),
                "up": float(sample[1]),
            }
        )
    return {
        "ok": bool(samples) and all(sample["up"] == 1 for sample in samples),
        "state": "missing" if not samples else ("up" if all(sample["up"] == 1 for sample in samples) else "down"),
        "samples": samples,
    }


def freshness_status(result: list[dict], max_age_seconds: int) -> dict:
    samples = []
    identity_complete = False
    fresh_complete = False
    for item in result:
        value = item.get("value") or []
        if len(value) < 2:
            continue
        labels = item.get("metric") or {}
        evaluation_time = float(value[0])
        source_time = float(value[1])
        missing_identity = sorted(label for label in IDENTITY_LABELS if not labels.get(label))
        identity_complete = identity_complete or not missing_identity
        age_seconds = max(0.0, evaluation_time - source_time)
        fresh_complete = fresh_complete or (age_seconds <= max_age_seconds and not missing_identity)
        samples.append(
            {
                "instance": labels.get("instance"),
                "age_seconds": age_seconds,
                "missing_identity": missing_identity,
            }
        )
    freshest_age = min((sample["age_seconds"] for sample in samples), default=None)
    return {
        "ok": fresh_complete,
        "max_age_seconds": max_age_seconds,
        "freshest_age_seconds": freshest_age,
        "identity_complete": identity_complete,
        "samples": samples,
    }


def native_target_status(base_url: str, gateway_id: str | None) -> dict:
    matchers = ['job="openclaw-gateways"']
    if gateway_id:
        matchers.append(f'gateway_id="{gateway_id}"')
    result = prometheus_query(base_url, "up{" + ",".join(matchers) + "}")
    return target_status(result)


def otel_freshness_status(base_url: str, gateway_id: str | None, max_age_seconds: int) -> dict:
    matchers = ['job="openclaw-gateway"']
    if gateway_id:
        matchers.append(f'gateway_id="{gateway_id}"')
    result = prometheus_query(
        base_url,
        "timestamp(target_info{" + ",".join(matchers) + "})",
    )
    return freshness_status(result, max_age_seconds)


def build_otlp_log_payload(marker: str, gateway_id: str | None, timestamp_ns: int) -> dict:
    resource_attributes = [
        {"key": "service.name", "value": {"stringValue": LOKI_ACCEPTANCE_SERVICE}},
        {"key": "agentswatch.acceptance", "value": {"stringValue": "loki-roundtrip"}},
    ]
    if gateway_id:
        resource_attributes.append({"key": "gateway_id", "value": {"stringValue": gateway_id}})
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": resource_attributes},
                "scopeLogs": [
                    {
                        "scope": {"name": "agentswatch-telemetry-acceptance"},
                        "logRecords": [
                            {
                                "timeUnixNano": str(timestamp_ns),
                                "severityNumber": 9,
                                "severityText": "INFO",
                                "body": {"stringValue": marker},
                                "attributes": [
                                    {
                                        "key": "agentswatch.acceptance.marker",
                                        "value": {"stringValue": marker},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def loki_query_url(base_url: str, marker: str, start_ns: int, end_ns: int) -> str:
    expression = f'{{service_name="{LOKI_ACCEPTANCE_SERVICE}"}} |= "{marker}"'
    params = urllib.parse.urlencode(
        {
            "query": expression,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": "20",
            "direction": "backward",
        }
    )
    return f"{base_url.rstrip('/')}/loki/api/v1/query_range?{params}"


def loki_contains_marker(payload: dict, marker: str) -> bool:
    if payload.get("status") != "success":
        return False
    result = ((payload.get("data") or {}).get("result") or [])
    for stream in result:
        for value in stream.get("values") or []:
            if any(marker in str(part) for part in value[1:]):
                return True
    return False


def authorization_headers(
    user: str | None,
    password: str | None,
    bearer_token: str | None,
) -> dict[str, str]:
    if bearer_token and (user or password):
        raise ValueError("choose either Loki bearer authentication or basic authentication")
    if user or password:
        if not user or password is None:
            raise ValueError("Loki basic authentication requires both user and password")
        encoded = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    if bearer_token:
        return {"Authorization": f"Bearer {bearer_token}"}
    return {}


def safe_error(error: Exception) -> str:
    code = getattr(error, "code", None)
    if code is not None:
        return f"HTTP {code}"
    return f"{type(error).__name__}: {error}"


def check_loki_roundtrip(
    otel_http_url: str,
    loki_base_url: str,
    gateway_id: str | None,
    query_headers: dict[str, str],
    timeout_seconds: float,
    poll_interval_seconds: float = 1.0,
) -> dict:
    marker = f"agentswatch-loki-acceptance-{uuid.uuid4().hex}"
    sent_at_ns = time.time_ns()
    otlp_headers = {}
    if gateway_id:
        otlp_headers["x-openclaw-gateway-id"] = gateway_id
    try:
        status_code = post_json(
            f"{otel_http_url.rstrip('/')}/v1/logs",
            build_otlp_log_payload(marker, gateway_id, sent_at_ns),
            otlp_headers,
        )
    except Exception as error:
        return {"ok": False, "stage": "otlp_write", "error": safe_error(error)}

    deadline = time.monotonic() + timeout_seconds
    last_error = None
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            query = loki_query_url(
                loki_base_url,
                marker,
                sent_at_ns - 10_000_000_000,
                time.time_ns() + 1_000_000_000,
            )
            payload = fetch_json(query, query_headers)
            if loki_contains_marker(payload, marker):
                return {
                    "ok": True,
                    "stage": "complete",
                    "otlp_status": status_code,
                    "query_attempts": attempts,
                }
            last_error = "acceptance record not returned"
        except Exception as error:
            last_error = safe_error(error)
        time.sleep(poll_interval_seconds)
    return {
        "ok": False,
        "stage": "loki_query",
        "otlp_status": status_code,
        "query_attempts": attempts,
        "error": last_error or "acceptance record not returned before timeout",
    }


def status(required: set[str], observed: set[str]) -> dict:
    present = sorted(required & observed)
    missing = sorted(required - observed)
    return {
        "ok": not missing,
        "present": present,
        "missing": missing,
    }


def acceptance_decision(
    *,
    skip_native: bool,
    require_usage: bool,
    otel_core_ok: bool,
    otel_fresh_ok: bool,
    otel_usage_ok: bool,
    native_endpoint_core_ok: bool,
    native_endpoint_usage_ok: bool,
    native_scrape_core_ok: bool,
    native_scrape_usage_ok: bool,
    native_target_ok: bool,
    loki_ok: bool,
) -> dict:
    core_ok = (
        otel_core_ok
        and otel_fresh_ok
        and (
            skip_native
            or (
                native_endpoint_core_ok
                and native_scrape_core_ok
                and native_target_ok
            )
        )
    )
    usage_paths_ok = (
        otel_usage_ok
        and (
            skip_native
            or (native_endpoint_usage_ok and native_scrape_usage_ok)
        )
    )
    usage_ok = not require_usage or usage_paths_ok
    parallel_ok = core_ok and usage_ok
    return {
        "usage": {
            "required": require_usage,
            "ok": usage_ok,
            "paths": {
                "otel_bridge": otel_usage_ok,
                "native_endpoint": None if skip_native else native_endpoint_usage_ok,
                "native_prometheus_metrics": None if skip_native else native_scrape_usage_ok,
            },
        },
        "parallel_ok": parallel_ok,
        "overall_ok": parallel_ok and loki_ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate fresh OpenClaw OTel, native Prometheus, and optional Loki data paths."
    )
    parser.add_argument("--gateway-url", default="http://127.0.0.1:16402", help="OpenClaw Gateway base URL")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090", help="Prometheus base URL")
    parser.add_argument("--token-file", help="Gateway bearer token file; defaults to prometheus/secrets/gateways/<gateway_id>.token")
    parser.add_argument("--gateway-id", help="Limit Prometheus validation to one managed gateway_id")
    parser.add_argument("--skip-native", action="store_true", help="Skip the direct Gateway endpoint and native scrape checks")
    parser.add_argument("--lookback-seconds", type=int, default=900, help="Prometheus series lookback window")
    parser.add_argument("--freshness-seconds", type=int, default=180, help="Maximum accepted age for OTel target_info")
    parser.add_argument(
        "--require-usage",
        action="store_true",
        help="Require usage metric families on every enabled OTel/native metrics path",
    )
    parser.add_argument("--check-loki", action="store_true", help="Write one synthetic OTLP log and query it from Loki")
    parser.add_argument("--require-loki", action="store_true", help="Run the Loki round-trip and fail when it is not queryable")
    parser.add_argument("--otel-http-url", default="http://127.0.0.1:4318", help="Collector OTLP/HTTP base URL")
    parser.add_argument(
        "--loki-query-url",
        default=os.environ.get("LOKI_QUERY_URL"),
        help="Loki base URL, or a Grafana datasource-proxy base ending in /uid/loki",
    )
    parser.add_argument("--loki-query-timeout-seconds", type=float, default=30)
    parser.add_argument("--loki-basic-auth-user")
    parser.add_argument("--loki-basic-auth-password-file")
    parser.add_argument(
        "--loki-basic-auth-password-env",
        help="Environment variable containing the Loki/Grafana password; the value is never printed",
    )
    parser.add_argument("--loki-bearer-token-file")
    args = parser.parse_args()
    if args.gateway_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.gateway_id):
        parser.error("--gateway-id contains unsupported characters")
    if args.lookback_seconds <= 0 or args.freshness_seconds <= 0:
        parser.error("lookback and freshness values must be positive")
    run_loki_check = args.check_loki or args.require_loki
    if run_loki_check and not args.loki_query_url:
        parser.error("--check-loki/--require-loki requires --loki-query-url or LOKI_QUERY_URL")

    password = None
    if args.loki_basic_auth_password_file:
        password = read_token(args.loki_basic_auth_password_file)
    if args.loki_basic_auth_password_env:
        if password is not None:
            parser.error("choose one Loki basic-auth password source")
        password = os.environ.get(args.loki_basic_auth_password_env)
        if password is None:
            parser.error(f"environment variable {args.loki_basic_auth_password_env} is not set")
    bearer_token = read_token(args.loki_bearer_token_file)
    try:
        loki_headers = authorization_headers(args.loki_basic_auth_user, password, bearer_token)
    except ValueError as error:
        parser.error(str(error))

    native_report = {"skipped": True}
    native_ok = True
    if not args.skip_native:
        token_file = args.token_file
        if not token_file and args.gateway_id:
            token_file = f"prometheus/secrets/gateways/{args.gateway_id}.token"
        try:
            token = read_token(token_file)
            native_text = fetch_text(
                urllib.parse.urljoin(args.gateway_url.rstrip("/") + "/", "api/diagnostics/prometheus"),
                token,
            )
            native_names = metric_names_from_text(native_text)
            native_report = {
                "core": status(NATIVE_CORE_METRICS, native_names),
                "usage": status(NATIVE_USAGE_METRICS, native_names),
            }
            native_report["usage"]["required"] = args.require_usage
            native_ok = native_report["core"]["ok"]
        except Exception as error:
            native_report = {"ok": False, "error": safe_error(error)}
            native_ok = False

    try:
        otel_names = prometheus_names(
            args.prometheus_url,
            args.gateway_id,
            "openclaw-gateway",
            args.lookback_seconds,
        )
        otel_report = {
            "core": status(OTEL_CORE_METRICS, otel_names),
            "usage": status(OTEL_USAGE_METRICS, otel_names),
            "freshness": otel_freshness_status(
                args.prometheus_url,
                args.gateway_id,
                args.freshness_seconds,
            ),
        }
        otel_report["usage"]["required"] = args.require_usage
    except Exception as error:
        otel_report = {"ok": False, "error": safe_error(error)}

    native_scrape_report = {"skipped": True}
    native_target_report = {"skipped": True}
    if not args.skip_native:
        try:
            native_scrape_names = prometheus_names(
                args.prometheus_url,
                args.gateway_id,
                "openclaw-gateways",
                args.lookback_seconds,
            )
            native_scrape_report = {
                "core": status(NATIVE_CORE_METRICS, native_scrape_names),
                "usage": status(NATIVE_USAGE_METRICS, native_scrape_names),
            }
            native_scrape_report["usage"]["required"] = args.require_usage
            native_target_report = native_target_status(args.prometheus_url, args.gateway_id)
        except Exception as error:
            native_scrape_report = {"ok": False, "error": safe_error(error)}
            native_target_report = {"ok": False, "error": safe_error(error)}

    otel_core_ok = bool((otel_report.get("core") or {}).get("ok"))
    otel_fresh_ok = bool((otel_report.get("freshness") or {}).get("ok"))
    otel_usage_ok = bool((otel_report.get("usage") or {}).get("ok"))
    native_scrape_ok = args.skip_native or bool((native_scrape_report.get("core") or {}).get("ok"))
    native_scrape_usage_ok = bool((native_scrape_report.get("usage") or {}).get("ok"))
    native_endpoint_usage_ok = bool((native_report.get("usage") or {}).get("ok"))
    native_target_ok = args.skip_native or bool(native_target_report.get("ok"))

    loki_report = {"skipped": True, "required": args.require_loki}
    if run_loki_check:
        loki_report = check_loki_roundtrip(
            args.otel_http_url,
            args.loki_query_url,
            args.gateway_id,
            loki_headers,
            args.loki_query_timeout_seconds,
        )
        loki_report["required"] = args.require_loki

    loki_ok = not args.require_loki or bool(loki_report.get("ok"))
    decision = acceptance_decision(
        skip_native=args.skip_native,
        require_usage=args.require_usage,
        otel_core_ok=otel_core_ok,
        otel_fresh_ok=otel_fresh_ok,
        otel_usage_ok=otel_usage_ok,
        native_endpoint_core_ok=native_ok,
        native_endpoint_usage_ok=native_endpoint_usage_ok,
        native_scrape_core_ok=native_scrape_ok,
        native_scrape_usage_ok=native_scrape_usage_ok,
        native_target_ok=native_target_ok,
        loki_ok=loki_ok,
    )
    parallel_report = {
        "skipped": args.skip_native,
        "ok": decision["parallel_ok"],
        "usage": decision["usage"],
        "paths": {
            "otel_bridge_fresh": otel_core_ok and otel_fresh_ok,
            "native_endpoint": native_ok if not args.skip_native else None,
            "native_prometheus_target": native_target_ok if not args.skip_native else None,
            "native_prometheus_metrics": native_scrape_ok if not args.skip_native else None,
        },
    }

    report = {
        "gateway_id": args.gateway_id,
        "native_endpoint": native_report,
        "prometheus": {
            "otel_bridge": otel_report,
            "native_scrape": native_scrape_report,
            "native_target": native_target_report,
            "parallel_validation": parallel_report,
        },
        "loki_roundtrip": loki_report,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if decision["overall_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
