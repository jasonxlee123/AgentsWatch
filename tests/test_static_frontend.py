import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentswatch_api.static_frontend import (
    STATIC_DIR_ENV,
    configured_static_directory,
    mount_configured_frontend,
)


class StaticFrontendConfigurationTests(unittest.TestCase):
    def test_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(configured_static_directory())

    def test_requires_an_absolute_built_directory(self):
        with patch.dict(os.environ, {STATIC_DIR_ENV: "frontend/dist"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "absolute path"):
                configured_static_directory()

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {STATIC_DIR_ENV: directory}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "built index.html"):
                    configured_static_directory()

    def test_resolves_a_built_frontend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("<!doctype html>", encoding="utf-8")
            with patch.dict(os.environ, {STATIC_DIR_ENV: str(root)}, clear=True):
                self.assertEqual(configured_static_directory(), root.resolve())

    def test_mount_serves_frontend_without_shadowing_api_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("<!doctype html><title>AgentsWatch</title>", encoding="utf-8")
            (root / "assets").mkdir()
            (root / "assets" / "app.js").write_text("window.AGENTSWATCH = true;", encoding="utf-8")
            app = FastAPI()

            @app.get("/api/ping")
            def ping():
                return {"status": "ok"}

            with patch.dict(os.environ, {STATIC_DIR_ENV: str(root)}, clear=True):
                mount_configured_frontend(app)
            client = TestClient(app)
            self.assertEqual(client.get("/api/ping").json(), {"status": "ok"})
            self.assertIn("AgentsWatch", client.get("/").text)
            self.assertIn("AGENTSWATCH", client.get("/assets/app.js").text)


if __name__ == "__main__":
    unittest.main()
