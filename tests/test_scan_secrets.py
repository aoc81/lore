"""Tests for scan_secrets.py: detection, placeholders, allowlisting."""
import re
import tempfile
import unittest
from pathlib import Path

import _util  # noqa: F401
import scan_secrets


def _scan(text, allow=()):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "entry.md"
        p.write_text(text, encoding="utf-8")
        return scan_secrets.scan_file(p, [re.compile(a) for a in allow])


class TestScanFile(unittest.TestCase):
    def test_detects_aws_key(self):
        fake = "AKIA" + "A" * 16  # shaped like a key, obviously synthetic
        found = _scan(f"the key {fake} leaked")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1], "AWS access key id")

    def test_redacts_output(self):
        fake = "AKIA" + "B" * 16
        (_n, _name, redacted) = _scan(f"x {fake}")[0]
        self.assertNotIn(fake, redacted)

    def test_placeholder_assignment_not_flagged(self):
        self.assertEqual(_scan("password = changeme"), [])
        self.assertEqual(_scan("api_key: <your-key-here>"), [])
        self.assertEqual(_scan("secret = ${MY_SECRET}"), [])

    def test_real_looking_assignment_flagged(self):
        found = _scan('password = "h8s7d6f5g4h3j2k1"')
        self.assertEqual(len(found), 1)

    def test_prefixed_secret_names_flagged(self):
        # `\b` cannot fire between `_` and a letter, so an earlier version of this
        # rule missed every prefixed spelling -- which is most of them in practice.
        for line in ('db_password = "h8s7d6f5g4h3j2k1"',
                     'my_api_key = "AbCd1234EfGh5678"',
                     'STRIPE_SECRET = "AbCd1234EfGh5678"',
                     'aws_secret_access_key = "wJalrXUtnFEMI7K7MDENGbPxRfiCYEX"',
                     "DATABASE_PASSWORD=s3cr3tp4ssw0rd"):
            with self.subTest(line=line):
                self.assertEqual(len(_scan(line)), 1)

    def test_prefixed_placeholder_still_not_flagged(self):
        # Widening the name must not cost the placeholder filter its job.
        self.assertEqual(_scan("db_password = changeme"), [])
        self.assertEqual(_scan('my_api_key = "your_key_here"'), [])

    def test_short_or_nonsecret_values_not_flagged(self):
        self.assertEqual(_scan("password: x"), [])
        self.assertEqual(_scan("secret_name = db"), [])

    def test_inline_allow(self):
        fake = "AKIA" + "C" * 16
        self.assertEqual(_scan(f"{fake}  lore:allow-secret"), [])

    def test_config_allow_regex(self):
        fake = "AKIA" + "D" * 16
        self.assertEqual(_scan(f"key {fake}", allow=[r"AKIAD+"]), [])

    def test_private_key_block(self):
        found = _scan("-----BEGIN RSA PRIVATE KEY-----")
        self.assertEqual(found[0][1], "Private key block")

    def test_anthropic_key_reported_once(self):
        fake = "sk-ant-" + "E" * 24  # synthetic, matches the shape only
        found = _scan(f"key {fake} leaked")
        self.assertEqual([f[1] for f in found], ["Anthropic key"])

    def test_openai_key_still_flagged(self):
        fake = "sk-proj-" + "F" * 24
        self.assertEqual([f[1] for f in _scan(f"key {fake}")], ["OpenAI key"])


if __name__ == "__main__":
    unittest.main()
