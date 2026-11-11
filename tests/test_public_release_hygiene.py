import tempfile
import unittest
from pathlib import Path

from scripts.check_public_release import load_denylist, path_finding, scan_bytes


class PublicReleaseHygieneTests(unittest.TestCase):
    def test_rejects_runtime_inventory_and_private_key_paths(self):
        self.assertIsNotNone(path_finding("cluster/lobsters.json"))
        self.assertIsNotNone(path_finding("private/id_ed25519"))
        self.assertIsNotNone(path_finding("credentials/client.key"))

    def test_detects_private_networks_and_key_patterns_without_echoing_values(self):
        private_ip = b"10" + b".20.30.40"
        key_value = b"s" + b"k-" + (b"a" * 24)

        findings = scan_bytes("fixture", private_ip + b"\n" + key_value)

        self.assertEqual(
            {finding.rule for finding in findings},
            {"private network address", "OpenAI-shaped key"},
        )
        self.assertTrue(all(private_ip.decode() not in repr(item) for item in findings))
        self.assertTrue(all(key_value.decode() not in repr(item) for item in findings))

    def test_allows_documentation_and_synthetic_networks(self):
        content = b"192.0.2.10\n10.0.0.20\n10.10.0.11\n172.30.0.1\n127.0.0.1\n0.0.0.0"
        self.assertEqual(scan_bytes("fixture", content), [])

    def test_private_denylist_reports_terms_without_echoing_values(self):
        term = b"synthetic-private-marker"
        findings = scan_bytes("fixture", b"prefix " + term + b" suffix", (term,))
        self.assertEqual(
            [finding.rule for finding in findings],
            ["private denylist term #1"],
        )
        self.assertNotIn(term.decode(), repr(findings))

    def test_private_denylist_file_ignores_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "denylist"
            path.write_text("# comment\n\nfirst\nsecond\n", encoding="utf-8")
            self.assertEqual(load_denylist(str(path)), (b"first", b"second"))

    def test_allows_explicit_fixture_sentinels_and_examples(self):
        content = b"fixture-redaction-sentinel\nhttps://api.example.invalid\n192.0.2.10"
        self.assertEqual(scan_bytes("fixture", content), [])
        self.assertIsNone(path_finding("deploy/management/.env.production.example"))


if __name__ == "__main__":
    unittest.main()
