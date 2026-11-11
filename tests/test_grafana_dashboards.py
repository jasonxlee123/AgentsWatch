import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_grafana_dashboards",
    ROOT / "scripts" / "generate_grafana_dashboards.py",
)
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def expressions(dashboard):
    return [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if target.get("expr")
    ]


class GrafanaDashboardTests(unittest.TestCase):
    def test_operational_queries_use_otel_prometheus_bridge_schema(self):
        dashboards = [
            generator.overview_dashboard(),
            generator.detail_dashboard(),
            generator.report_dashboard(),
        ]
        rendered = "\n".join(expr for dashboard in dashboards for expr in expressions(dashboard))

        self.assertIn('openclaw_tokens_total{job="openclaw-gateway"', rendered)
        self.assertIn("openclaw_model_call_duration_ms_milliseconds_count", rendered)
        self.assertIn("openclaw_session_turn_created_total", rendered)
        self.assertNotIn("openclaw_model_tokens_total", rendered)
        self.assertNotIn("openclaw_model_call_total", rendered)

    def test_native_scrape_health_remains_separate_from_otel_data(self):
        overview = "\n".join(expressions(generator.overview_dashboard()))

        self.assertIn('target_info{job="openclaw-gateway"', overview)
        self.assertIn('up{job="openclaw-gateways"', overview)
        self.assertIn("agentswatch_gateway_info{inventory_state=", overview)

    def test_identity_and_filters_do_not_depend_on_native_scrape(self):
        dashboards = [
            generator.overview_dashboard(),
            generator.detail_dashboard(),
            generator.report_dashboard(),
        ]
        rendered = json.dumps(dashboards, ensure_ascii=False)

        self.assertIn("label_values(agentswatch_gateway_info", rendered)
        self.assertNotIn('label_values(up{job=\\"openclaw-gateways\\"', rendered)

    def test_otel_queries_use_gateway_identity_not_mutable_display_metadata(self):
        dashboards = [
            generator.overview_dashboard(),
            generator.detail_dashboard(),
            generator.report_dashboard(),
        ]
        rendered = "\n".join(
            expr for dashboard in dashboards for expr in expressions(dashboard)
        )

        self.assertNotIn('job="openclaw-gateway",cluster=', rendered)
        self.assertNotIn('job="openclaw-gateway",team=', rendered)
        self.assertIn("and on (gateway_id)", rendered)

    def test_report_gateway_table_joins_once_on_gateway_id(self):
        dashboard = generator.report_dashboard()
        report = next(panel for panel in dashboard["panels"] if panel.get("title") == "龙虾运营汇总")

        self.assertEqual([target["refId"] for target in report["targets"]], list("ABCDEFGHIJK"))
        self.assertEqual(
            [transform["id"] for transform in report["transformations"]],
            ["joinByField", "organize"],
        )
        self.assertEqual(report["transformations"][0]["options"]["byField"], "gateway_id")


if __name__ == "__main__":
    unittest.main()
