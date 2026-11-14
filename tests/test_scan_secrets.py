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

    def test_markdown_punctuation_does_not_defeat_the_filter(self):
        # The store is Markdown, so the closing backtick of `inline code` and
        # the sentence's own period land inside the captured value: the filter
        # has to see "False", not "False`).".
        self.assertEqual(_scan("`import_secrets=False`)."), [])
        self.assertEqual(_scan("set `api_key=${MY_KEY}`."), [])
        self.assertEqual(_scan('(password = "changeme"),'), [])

    def test_interpolation_and_substitution_not_flagged(self):
        # Only the bare `${VAR}` form used to be recognised; a learning quotes
        # the others far more often than it quotes a literal.
        for line in ("api_key=${MY_KEY:-${OTHER:-}}",
                     "api_key=$(docker exec dir printenv ADMIN_TOKEN)",
                     "export API_KEY=$(grep API_KEY .env.local | cut -d= -f2)",
                     "$env:API_KEY = (Get-Content .env.local | Select-String k)",
                     "api_key=$MY_KEY",
                     "api_key=%MY_KEY%",
                     "API_SECRET='<the real token>'",
                     # the inner half of an expansion, reached through its `:-`
                     "`${PUBLIC_API_KEY:-${FALLBACK:-}}` also matters because"):
            with self.subTest(line=line):
                self.assertEqual(_scan(line), [])

    def test_self_labelled_dev_values_not_flagged(self):
        # The placeholder words were prefix-anchored, so the spellings that
        # actually occur -- a committed local default, not a bare `changeme` --
        # all came out as findings.
        for line in ("SESSION_SECRET=local-test",
                     "api_key=acme-local-admin-token-change-if-shared",
                     "password=dev-only-not-real",
                     "client_secret: staging-value-here",
                     "password = localhost:5432"):
            with self.subTest(line=line):
                self.assertEqual(_scan(line), [])

    def test_punctuation_only_placeholders_still_filtered(self):
        # Unwrapping must not strip the value away: `....` and `****` are
        # placeholders in their own right, so the raw value is tried too.
        self.assertEqual(_scan('password = "...."'), [])
        self.assertEqual(_scan("password = ****"), [])

    def test_widened_filter_keeps_its_false_negatives_out(self):
        # Every change above SUPPRESSES findings, so this is the direction that
        # needs a guard: a self-labelling word only disarms the value when it is
        # a whole segment of it, and a bracketed value is only code when it
        # contains a space.
        for line in ("api_key=Local7fGh5678Ab",
                     "api_key = (h8s7d6f5g4h3j2k1)",
                     'password = "Changed8f7d6s5a4"',
                     'api_key = "Testable8f7d6s5a4"',
                     "password: -h8s7d6f5g4h3j2k1",
                     "api_key=Devi0us7fGh5678Ab"):
            with self.subTest(line=line):
                self.assertEqual(len(_scan(line)), 1, line)

    def test_a_label_does_not_disarm_a_credential_shaped_value(self):
        # 0.7.6 let a self-labelling word at either end disarm whatever it was
        # attached to. A staging credential is still a credential: the label
        # only counts when the rest of the value is plain lower-case words.
        for line in ("password=Hunter2-staging",
                     "DB_PASSWORD=Xk9fj3kd0s-dev",
                     "api_key=dev-8f7d6s5a4kd93jf7s6d5",
                     "api_key=staging-8f7d6s5a4kd93jf7s6d5",
                     "api_key=local-8f7d6s5a4kd93jf7s6d5",
                     "password=8f7d6s5a4kd93jf.local",
                     "password=HunterTwo-staging",
                     "password=$up3rs3cret99",
                     "client_secret=stockexchangeit2024",
                     "password = (hunter2 is the real one)"):
            with self.subTest(line=line):
                self.assertEqual(len(_scan(line)), 1, line)

    def test_word_only_labelled_values_not_flagged(self):
        for line in ("password=postgres_dev",
                     "password=db.internal.local",
                     "api_key=$API_KEY2",
                     "client_secret=CHANGE_ME"):
            with self.subTest(line=line):
                self.assertEqual(_scan(line), [])

    def test_nothing_0_7_5_filtered_became_a_finding(self):
        # A precision fix may only ADD suppression. `test[_-].*` was dropped in
        # the 0.7.6 rewrite, and the bracket alternatives matched lines the
        # general branch never had (`<>` is too short for it; so is `(ask`).
        for line in ("password=test_p@ss/w0rd=",
                     "api_key=test-8f7d6s5a4kd93jf7s6d5",
                     "if password = <> then",
                     "secret: <>",
                     "Password: (ask Will)",
                     "token_secret = ()"):
            with self.subTest(line=line):
                self.assertEqual(_scan(line), [])

    def test_vendor_rules_unaffected_by_unwrapping(self):
        # `_is_placeholder` judges group(0) for the rules with no capture group,
        # so the unwrapping runs on their matches too.
        fake = "AKIA" + "G" * 16
        self.assertEqual([f[1] for f in _scan(f"`{fake}`.")],
                         ["AWS access key id"])

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
