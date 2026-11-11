import json
import unittest
from pathlib import Path

from agentswatch_api import __version__ as api_version
from agentswatch_api.main import app as api_app
from agentswatch_mcp import __version__ as mcp_version
from agentswatch_observability import __version__ as observability_version


ROOT = Path(__file__).resolve().parents[1]


class ReleaseVersionTests(unittest.TestCase):
    def test_release_versions_match(self):
        expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        frontend = json.loads(
            (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
        )["version"]

        self.assertEqual(expected, "2.2.0")
        self.assertEqual(
            {api_version, api_app.version, mcp_version, observability_version, frontend},
            {expected},
        )


if __name__ == "__main__":
    unittest.main()
