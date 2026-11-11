import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LokiConfigTests(unittest.TestCase):
    def test_wal_threshold_is_configurable_with_conservative_default(self):
        config = (ROOT / "loki" / "loki-config.yaml").read_text()
        compose = (ROOT / "docker-compose.yml").read_text()
        env_example = (ROOT / ".env.example").read_text()

        self.assertIn(
            "disk_full_threshold: ${LOKI_WAL_DISK_FULL_THRESHOLD}", config
        )
        self.assertIn(
            "LOKI_WAL_DISK_FULL_THRESHOLD: ${LOKI_WAL_DISK_FULL_THRESHOLD:-0.90}",
            compose,
        )
        self.assertIn("LOKI_WAL_DISK_FULL_THRESHOLD=0.90", env_example)


if __name__ == "__main__":
    unittest.main()
