"""Tests for the secret scan scripts.

Covers:
- gh-comment.sh input validation
- dd-log-search.sh query building and output redaction
- gcs-scan.sh output redaction
- patterns.txt validity
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).parent.parent.parent / "scripts" / "secret-scan"
)


# ---------------------------------------------------------------------------
# gh-comment.sh — input validation
# ---------------------------------------------------------------------------


class TestGhComment:
    """Tests for gh-comment.sh input validation (no network calls)."""

    script = str(SCRIPTS_DIR / "gh-comment.sh")

    def _run(self, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        env = {"GH_TOKEN": "fake-token", "PATH": "/usr/bin:/bin:/usr/local/bin"}
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", self.script, *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_rejects_missing_subcommand(self):
        result = self._run()
        assert result.returncode != 0

    def test_rejects_unknown_subcommand(self):
        result = self._run("delete", "OpenHands/evaluation", "42")
        assert result.returncode != 0
        assert "unknown subcommand" in result.stderr

    def test_rejects_non_numeric_issue(self):
        result = self._run("comment", "OpenHands/evaluation", "abc", "body")
        assert result.returncode != 0
        assert "numeric" in result.stderr

    def test_rejects_malformed_repo(self):
        result = self._run("comment", "not-a-repo", "42", "body")
        assert result.returncode != 0
        assert "owner/repo" in result.stderr

    def test_rejects_repo_with_extra_slash(self):
        result = self._run("comment", "a/b/c", "42", "body")
        assert result.returncode != 0
        assert "owner/repo" in result.stderr

    def test_rejects_missing_token(self):
        result = subprocess.run(
            ["bash", self.script, "comment", "OpenHands/evaluation", "42", "body"],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
        assert result.returncode != 0
        assert "GH_TOKEN" in result.stderr

    def test_view_issue_rejects_non_numeric(self):
        result = self._run("view-issue", "OpenHands/evaluation", "abc")
        assert result.returncode != 0
        assert "numeric" in result.stderr

    def test_create_pr_rejects_malformed_repo(self):
        result = self._run("create-pr", "bad", "title", "body", "head", "main")
        assert result.returncode != 0
        assert "owner/repo" in result.stderr


# ---------------------------------------------------------------------------
# patterns.txt — validity
# ---------------------------------------------------------------------------


class TestPatterns:
    """Tests that patterns.txt contains valid regex and expected patterns."""

    patterns_file = SCRIPTS_DIR / "patterns.txt"

    def _load_patterns(self) -> list[str]:
        lines = self.patterns_file.read_text().splitlines()
        return [
            line
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_all_patterns_are_valid_regex(self):
        for pattern in self._load_patterns():
            try:
                re.compile(pattern)
            except re.error as e:
                pytest.fail(f"Invalid regex in patterns.txt: {pattern!r} — {e}")

    def test_contains_key_service_patterns(self):
        """Ensure we cover the major secret types from evaluation#383."""
        text = self.patterns_file.read_text()
        for prefix in [
            "sk-proj-",
            "sk-ant-",
            "sk-oh-",
            "tvly-",
            "sk-or-v1-",
            "gsk_",
            "hf_",
            "ghp_",
            "AKIA",
        ]:
            assert prefix in text, f"Missing pattern for {prefix}"

    def test_no_overly_generic_patterns(self):
        """Verify we removed the patterns flagged in review."""
        for pattern in self._load_patterns():
            # These were removed because they cause false positive explosion
            assert not pattern.startswith("api_key"), (
                f"Generic pattern should be removed: {pattern}"
            )
            assert not pattern.startswith("password"), (
                f"Generic pattern should be removed: {pattern}"
            )
            assert not pattern.startswith("secret["), (
                f"Generic pattern should be removed: {pattern}"
            )
            assert not pattern.startswith("token["), (
                f"Generic pattern should be removed: {pattern}"
            )


# ---------------------------------------------------------------------------
# dd-log-search.sh — query building and redaction
# ---------------------------------------------------------------------------


class TestDdLogSearchRedaction:
    """Test that dd-log-search.sh redaction logic works via jq."""

    def test_jq_redaction_patterns(self):
        """Run the jq redaction filter standalone against known secrets."""
        # This is the same jq filter used in dd-log-search.sh
        jq_filter = """
        .message
        | gsub("(?i)sk-proj-[A-Za-z0-9_-]{4}[A-Za-z0-9_-]+"; "sk-proj-****[REDACTED]")
        | gsub("(?i)sk-ant-[A-Za-z0-9_-]{4}[A-Za-z0-9_-]+"; "sk-ant-****[REDACTED]")
        | gsub("(?i)sk-oh-[A-Za-z0-9_-]{4}[A-Za-z0-9_-]+"; "sk-oh-****[REDACTED]")
        | gsub("(?i)sk-or-v1-[A-Za-z0-9_-]{4}[A-Za-z0-9_-]+"; "sk-or-v1-****[REDACTED]")
        | gsub("(?i)ghp_[A-Za-z0-9]{4}[A-Za-z0-9]+"; "ghp_****[REDACTED]")
        | gsub("(?i)github_pat_[A-Za-z0-9_]{4}[A-Za-z0-9_]+"; "github_pat_****[REDACTED]")
        | gsub("AKIA[0-9A-Z]{4}[0-9A-Z]+"; "AKIA****[REDACTED]")
        | gsub("(?i)tvly-[A-Za-z0-9]{4}[A-Za-z0-9]+"; "tvly-****[REDACTED]")
        | gsub("(?i)gsk_[A-Za-z0-9]{4}[A-Za-z0-9]+"; "gsk_****[REDACTED]")
        | gsub("(?i)hf_[A-Za-z0-9]{4}[A-Za-z0-9]+"; "hf_****[REDACTED]")
        | gsub("(?i)tgp_v1_[A-Za-z0-9]{4}[A-Za-z0-9]+"; "tgp_v1_****[REDACTED]")
        """

        test_cases = [
            (
                "Leaked key: sk-proj-abc1234567890abcdef1234567890",
                "sk-proj-****[REDACTED]",
            ),
            (
                "Anthropic: sk-ant-api03-xyzzy1234567890",
                "sk-ant-****[REDACTED]",
            ),
            (
                "Session: sk-oh-sess-abc123def456",
                "sk-oh-****[REDACTED]",
            ),
            (
                "GitHub PAT: ghp_abcd1234567890123456789012345678901234",
                "ghp_****[REDACTED]",
            ),
            (
                "AWS key: AKIAIOSFODNN7EXAMPLE",
                "AKIA****[REDACTED]",
            ),
            (
                "Tavily: tvly-abcd1234567890123456",
                "tvly-****[REDACTED]",
            ),
            (
                "No secrets here, just a normal log line",
                None,  # should remain unchanged
            ),
        ]

        for message, expected_fragment in test_cases:
            input_json = json.dumps({"message": message})
            result = subprocess.run(
                ["jq", "-r", jq_filter],
                input=input_json,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"jq failed for input {message!r}: {result.stderr}"
            )
            output = result.stdout.strip()

            if expected_fragment is None:
                # No redaction should have happened
                assert output == message, (
                    f"Unexpected redaction: {message!r} -> {output!r}"
                )
            else:
                assert expected_fragment in output, (
                    f"Expected {expected_fragment!r} in output for "
                    f"{message!r}, got {output!r}"
                )
                # Ensure the full original secret is NOT in the output
                assert message not in output or expected_fragment is None, (
                    f"Full secret leaked through: {output!r}"
                )

    def test_dd_script_rejects_missing_env(self):
        """dd-log-search.sh should fail if DD_API_KEY/DD_APP_KEY missing."""
        result = subprocess.run(
            [
                "bash",
                str(SCRIPTS_DIR / "dd-log-search.sh"),
                "2026-04-01T00:00:00Z",
                "2026-04-02T00:00:00Z",
            ],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
        assert result.returncode != 0
        assert "DD_API_KEY" in result.stderr


# ---------------------------------------------------------------------------
# gcs-scan.sh — redaction in sed pipeline
# ---------------------------------------------------------------------------


class TestGcsScanRedaction:
    """Test that the sed redaction pipeline in gcs-scan.sh works."""

    def test_sed_redaction_patterns(self):
        """Run the sed redaction command against known secrets."""
        # This is the same sed pipeline used in gcs-scan.sh
        sed_cmd = (
            r"s/(sk-[a-zA-Z0-9_-]{4})[a-zA-Z0-9_-]+/\1[REDACTED]/g; "
            r"s/(sk-ant-[a-zA-Z0-9_-]{4})[a-zA-Z0-9_-]+/\1[REDACTED]/g; "
            r"s/(sk-proj-[a-zA-Z0-9_-]{4})[a-zA-Z0-9_-]+/\1[REDACTED]/g; "
            r"s/(sk-oh-[a-zA-Z0-9_-]{4})[a-zA-Z0-9_-]+/\1[REDACTED]/g; "
            r"s/(sk-or-v1-[a-zA-Z0-9_-]{4})[a-zA-Z0-9_-]+/\1[REDACTED]/g; "
            r"s/(ghp_[a-zA-Z0-9]{4})[a-zA-Z0-9]+/\1[REDACTED]/g; "
            r"s/(github_pat_[a-zA-Z0-9_]{4})[a-zA-Z0-9_]+/\1[REDACTED]/g; "
            r"s/(AKIA[0-9A-Z]{4})[0-9A-Z]+/\1[REDACTED]/g; "
            r"s/(tvly-[a-zA-Z0-9]{4})[a-zA-Z0-9]+/\1[REDACTED]/g; "
            r"s/(gsk_[a-zA-Z0-9]{4})[a-zA-Z0-9]+/\1[REDACTED]/g; "
            r"s/(hf_[a-zA-Z0-9]{4})[a-zA-Z0-9]+/\1[REDACTED]/g; "
            r"s/(tgp_v1_[a-zA-Z0-9]{4})[a-zA-Z0-9]+/\1[REDACTED]/g; "
            r"s/(=['\''\"']?[a-zA-Z0-9_-]{4})[a-zA-Z0-9_-]{16,}/\1[REDACTED]/g"
        )

        test_cases = [
            (
                "42:  api_key=sk-proj-abc1234567890abcdef",
                "sk-proj-",
                "sk-proj-abc1234567890abcdef",
            ),
            (
                "10:  token: ghp_abcd1234567890123456789012345678901234",
                "ghp_",
                "ghp_abcd1234567890123456789012345678901234",
            ),
            (
                "5:  key: AKIAIOSFODNN7EXAMPLE",
                "AKIA",
                "AKIAIOSFODNN7EXAMPLE",
            ),
        ]

        for line, _prefix_fragment, full_secret in test_cases:
            result = subprocess.run(
                ["sed", "-E", sed_cmd],
                input=line,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            output = result.stdout.strip()
            # Full secret must not appear
            assert full_secret not in output, (
                f"Secret leaked: {full_secret!r} in {output!r}"
            )
            # Redacted marker must appear
            assert "[REDACTED]" in output, (
                f"Missing [REDACTED] in output: {output!r}"
            )
