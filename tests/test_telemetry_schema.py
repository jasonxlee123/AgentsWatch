import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_openclaw_telemetry",
    ROOT / "scripts" / "check_openclaw_telemetry.py",
)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class TelemetrySchemaTests(unittest.TestCase):
    def test_otel_bridge_schema_matches_collector_prometheus_names(self):
        self.assertIn("openclaw_tokens_total", checker.OTEL_USAGE_METRICS)
        self.assertIn(
            "openclaw_model_call_duration_ms_milliseconds",
            checker.OTEL_USAGE_METRICS,
        )
        self.assertIn("target_info", checker.OTEL_CORE_METRICS)

    def test_alert_rules_use_otel_bridge_for_operational_metrics(self):
        rules = (ROOT / "prometheus" / "alerts" / "openclaw.rules.yml").read_text()

        self.assertIn('openclaw_tokens_total{job="openclaw-gateway"', rules)
        self.assertIn("openclaw_model_call_duration_ms_milliseconds_count", rules)
        self.assertIn('native_scrape_expected="true"', rules)
        self.assertNotIn("openclaw_model_tokens_total", rules)
        self.assertNotIn("openclaw_model_call_total", rules)

    def test_native_target_requires_a_current_up_sample(self):
        missing = checker.target_status([])
        down = checker.target_status(
            [
                {
                    "metric": {"instance": "host.docker.internal:16402"},
                    "value": [1000, "0"],
                }
            ]
        )
        up = checker.target_status(
            [
                {
                    "metric": {"instance": "host.docker.internal:16402"},
                    "value": [1000, "1"],
                }
            ]
        )

        self.assertFalse(missing["ok"])
        self.assertEqual(missing["state"], "missing")
        self.assertFalse(down["ok"])
        self.assertEqual(down["state"], "down")
        self.assertTrue(up["ok"])
        self.assertEqual(up["state"], "up")

    def test_otel_freshness_rejects_stale_or_incomplete_identity(self):
        complete_labels = {
            "instance": "runtime-1",
            "gateway_id": "local-macbook",
            "runtime_id": "runtime-1",
            "server_id": "server-1",
            "primary_agent_id": "agent-1",
            "agent_deployment_id": "deployment-1",
        }

        fresh = checker.freshness_status(
            [{"metric": complete_labels, "value": [1000, "950"]}],
            180,
        )
        stale = checker.freshness_status(
            [{"metric": complete_labels, "value": [1000, "700"]}],
            180,
        )
        incomplete = checker.freshness_status(
            [
                {
                    "metric": {"gateway_id": "local-macbook", "instance": "runtime-1"},
                    "value": [1000, "990"],
                }
            ],
            180,
        )
        split_false_positive = checker.freshness_status(
            [
                {
                    "metric": complete_labels,
                    "value": [1000, "700"],
                },
                {
                    "metric": {"gateway_id": "local-macbook", "instance": "runtime-1"},
                    "value": [1000, "990"],
                },
            ],
            180,
        )

        self.assertTrue(fresh["ok"])
        self.assertFalse(stale["ok"])
        self.assertFalse(incomplete["ok"])
        self.assertFalse(incomplete["identity_complete"])
        self.assertFalse(split_false_positive["ok"])

    def test_loki_health_response_cannot_fake_a_round_trip(self):
        marker = "agentswatch-loki-acceptance-test"
        health_payload = {"status": "success"}
        query_payload = {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {"service_name": checker.LOKI_ACCEPTANCE_SERVICE},
                        "values": [["1000000000", marker]],
                    }
                ],
            },
        }

        self.assertFalse(checker.loki_contains_marker(health_payload, marker))
        self.assertTrue(checker.loki_contains_marker(query_payload, marker))
        self.assertFalse(checker.loki_contains_marker(query_payload, marker + "-missing"))

    def test_loki_acceptance_payload_contains_only_synthetic_content(self):
        marker = "agentswatch-loki-acceptance-test"
        payload = checker.build_otlp_log_payload(marker, "local-macbook", 123)
        serialized = str(payload)

        self.assertIn(marker, serialized)
        self.assertIn(checker.LOKI_ACCEPTANCE_SERVICE, serialized)
        self.assertIn("local-macbook", serialized)
        self.assertNotIn("authorization", serialized.lower())
        self.assertNotIn("token", serialized.lower())

    def test_loki_round_trip_requires_the_written_marker_to_be_queryable(self):
        marker = "agentswatch-loki-acceptance-fixed"
        query_payload = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "values": [["1000000000", marker]],
                    }
                ]
            },
        }
        with mock.patch.object(
            checker.uuid,
            "uuid4",
            return_value=SimpleNamespace(hex="fixed"),
        ), mock.patch.object(checker, "post_json", return_value=200), mock.patch.object(
            checker,
            "fetch_json",
            return_value=query_payload,
        ):
            report = checker.check_loki_roundtrip(
                "http://collector:4318",
                "http://loki:3100",
                "local-macbook",
                {},
                1,
                poll_interval_seconds=0,
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["stage"], "complete")
        self.assertEqual(report["otlp_status"], 200)

    def test_missing_usage_is_non_blocking_by_default(self):
        decision = checker.acceptance_decision(
            skip_native=False,
            require_usage=False,
            otel_core_ok=True,
            otel_fresh_ok=True,
            otel_usage_ok=False,
            native_endpoint_core_ok=True,
            native_endpoint_usage_ok=False,
            native_scrape_core_ok=True,
            native_scrape_usage_ok=False,
            native_target_ok=True,
            loki_ok=True,
        )

        self.assertTrue(decision["usage"]["ok"])
        self.assertFalse(decision["usage"]["required"])
        self.assertTrue(decision["parallel_ok"])
        self.assertTrue(decision["overall_ok"])

    def test_require_usage_fails_parallel_and_overall_when_any_path_is_missing(self):
        decision = checker.acceptance_decision(
            skip_native=False,
            require_usage=True,
            otel_core_ok=True,
            otel_fresh_ok=True,
            otel_usage_ok=True,
            native_endpoint_core_ok=True,
            native_endpoint_usage_ok=True,
            native_scrape_core_ok=True,
            native_scrape_usage_ok=False,
            native_target_ok=True,
            loki_ok=True,
        )

        self.assertTrue(decision["usage"]["required"])
        self.assertFalse(decision["usage"]["ok"])
        self.assertFalse(decision["usage"]["paths"]["native_prometheus_metrics"])
        self.assertFalse(decision["parallel_ok"])
        self.assertFalse(decision["overall_ok"])

    def test_skip_native_strict_usage_only_requires_otel_usage(self):
        passing = checker.acceptance_decision(
            skip_native=True,
            require_usage=True,
            otel_core_ok=True,
            otel_fresh_ok=True,
            otel_usage_ok=True,
            native_endpoint_core_ok=False,
            native_endpoint_usage_ok=False,
            native_scrape_core_ok=False,
            native_scrape_usage_ok=False,
            native_target_ok=False,
            loki_ok=True,
        )
        failing = checker.acceptance_decision(
            skip_native=True,
            require_usage=True,
            otel_core_ok=True,
            otel_fresh_ok=True,
            otel_usage_ok=False,
            native_endpoint_core_ok=True,
            native_endpoint_usage_ok=True,
            native_scrape_core_ok=True,
            native_scrape_usage_ok=True,
            native_target_ok=True,
            loki_ok=True,
        )

        self.assertTrue(passing["overall_ok"])
        self.assertIsNone(passing["usage"]["paths"]["native_endpoint"])
        self.assertIsNone(passing["usage"]["paths"]["native_prometheus_metrics"])
        self.assertFalse(failing["overall_ok"])


if __name__ == "__main__":
    unittest.main()
