import base64
import secrets
import unittest

from agentswatch_api.runtime_web_access import CredentialCipher, RuntimeWebAccessService


class RuntimeWebAccessUnitTests(unittest.TestCase):
    def test_credentials_are_encrypted_and_round_trip(self):
        key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        cipher = CredentialCipher(key)
        encrypted = cipher.encrypt({"username": "operator", "password": "highly-secret"})

        self.assertNotIn("operator", encrypted)
        self.assertNotIn("highly-secret", encrypted)
        self.assertEqual(
            cipher.decrypt(encrypted),
            {"username": "operator", "password": "highly-secret"},
        )

    def test_invalid_key_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "URL-safe base64"):
            CredentialCipher("not-a-valid-key")

    def test_direct_url_and_base_path_validation_reject_credentials_or_fragments(self):
        RuntimeWebAccessService._validate_direct_url("https://runtime.example.test:18789")
        RuntimeWebAccessService._validate_base_path("/openclaw")
        with self.assertRaisesRegex(ValueError, "only scheme"):
            RuntimeWebAccessService._validate_direct_url("https://user:secret@example.test/path#token=x")
        with self.assertRaisesRegex(ValueError, "absolute URL path"):
            RuntimeWebAccessService._validate_base_path("https://example.test/path")

    def test_tailscale_serve_requires_https(self):
        values = {
            "endpoint_code": "ignored", "display_name": "Control UI",
            "access_mode": "tailscale_serve", "scheme": "https",
            "direct_url": "http://gateway.tail.example", "remote_host": "127.0.0.1",
            "port_override": None, "base_path": "/", "auth_mode": "none",
            "enabled": True, "is_primary": False, "row_version": None,
        }
        with self.assertRaisesRegex(ValueError, "https URL"):
            RuntimeWebAccessService(database=object()).upsert_endpoint(
                __import__("uuid").uuid4(), values, {},
            )


if __name__ == "__main__":
    unittest.main()
