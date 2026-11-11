import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

from agentswatch_api.server_credentials import (
    ServerCredentialCipher,
    validate_ssh_connection_host,
    validate_ssh_host_keys,
)
from agentswatch_api.server_management import ServerManagementService
from agentswatch_automation import AutomationRunner


class ServerCredentialTests(unittest.TestCase):
    def test_password_is_encrypted_and_round_trips(self):
        cipher = ServerCredentialCipher(Fernet.generate_key().decode())
        encrypted = cipher.encrypt("persistent-server-password")
        self.assertNotIn("persistent-server-password", encrypted)
        self.assertEqual(cipher.decrypt(encrypted), "persistent-server-password")

    def test_invalid_encryption_key_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "URL-safe base64"):
            ServerCredentialCipher("invalid")

    def test_host_key_is_pinned_to_the_configured_endpoint(self):
        key_data = base64.b64encode(b"test-ed25519-host-key").decode()
        keys, fingerprints = validate_ssh_host_keys(
            "10.0.0.9", 2222, [f"[10.0.0.9]:2222 ssh-ed25519 {key_data}"],
        )
        self.assertEqual(keys, [f"[10.0.0.9]:2222 ssh-ed25519 {key_data}"])
        self.assertRegex(fingerprints[0], r"^ssh-ed25519 SHA256:")
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_ssh_host_keys(
                "10.0.0.10", 2222, [f"[10.0.0.9]:2222 ssh-ed25519 {key_data}"],
            )

    def test_loopback_ssh_hosts_require_explicit_local_development_opt_in(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for host in (
                "127.0.0.1", "127.25.1.9", "::1", "::ffff:127.0.0.1",
                "localhost", "localhost.localdomain", "API.LOCALHOST.",
            ):
                with self.subTest(host=host), self.assertRaisesRegex(ValueError, "loopback"):
                    validate_ssh_connection_host(host)
            self.assertEqual(validate_ssh_connection_host("192.0.2.15"), "192.0.2.15")

        with mock.patch.dict(
            os.environ, {"AGENTSWATCH_ALLOW_LOCAL_CONNECTION": "true"},
        ):
            self.assertEqual(validate_ssh_connection_host(" localhost "), "localhost")
            with self.assertRaisesRegex(ValueError, "numeric IP notation"):
                validate_ssh_connection_host("127.1")

    def test_ssh_host_rejects_legacy_numeric_unspecified_and_multicast_addresses(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for host in (
                "127.1", "2130706433", "017700000001", "0177.0.0.1",
                "0x7f000001", "0x7f.0.0.1", "0", "0.0.0.0", "::",
                "224.0.0.1", "ff02::1",
            ):
                with self.subTest(host=host), self.assertRaisesRegex(
                    ValueError, "numeric IP notation|unspecified or multicast",
                ):
                    validate_ssh_connection_host(host)

    def test_save_connection_enforces_loopback_policy_before_database_access(self):
        database = mock.Mock()
        service = ServerManagementService(database=database)
        with mock.patch.dict(
            os.environ, {"AGENTSWATCH_ALLOW_LOCAL_CONNECTION": "false"},
        ):
            with self.assertRaisesRegex(ValueError, "loopback"):
                service.save_connection("unused", {
                    "connection_type": "ssh",
                    "host": "localhost",
                    "port": 22,
                    "username": "ops",
                    "credential_ref": None,
                    "use_become": False,
                })
        database.connection.assert_not_called()

    def test_bootstrap_inventory_keeps_password_transient_and_pins_known_hosts(self):
        key_data = base64.b64encode(b"test-ed25519-host-key").decode()
        with tempfile.TemporaryDirectory() as directory:
            known_hosts = Path(directory) / "known_hosts"
            inventory = AutomationRunner._inventory([{
                "server_id": "server-1",
                "address": "10.0.0.9",
                "connection": "ssh",
                "port": 22,
                "user": "ops",
                "password": "server-password",
                "become_password": "server-password",
                "agentswatch_bootstrap_public_key": "ssh-ed25519 public-key",
                "ssh_host_keys": [f"10.0.0.9 ssh-ed25519 {key_data}"],
            }], known_hosts)
        host = inventory["all"]["hosts"]["server-1"]
        self.assertEqual(host["ansible_password"], "server-password")
        self.assertEqual(host["ansible_become_password"], "server-password")
        self.assertEqual(host["agentswatch_bootstrap_public_key"], "ssh-ed25519 public-key")
        self.assertIn("StrictHostKeyChecking=yes", host["ansible_ssh_common_args"])


if __name__ == "__main__":
    unittest.main()
