"""Unit + light-integration tests for the PostHog self-healing example.

The example runs standalone (its GitHub Actions job checks out the repo and runs
the modules directly), so we import its modules by appending the example
directory to ``sys.path`` -- the same pattern as ``tests/cross/test_todo_scanner``.

The safety-critical behaviours proven here: PII/injection coercion, one-issue-
per-fingerprint aggregation across releases, allowlist eligibility, guardrail
logic (cooldown, kill switch), redaction of the issue body, and the
deterministic red->green verification gate.
"""

import subprocess
import sys
from pathlib import Path

import pytest


_EXAMPLE = (
    Path(__file__).parent.parent.parent
    / "examples"
    / "03_github_workflows"
    / "05_posthog_debugging"
)
sys.path.insert(0, str(_EXAMPLE))

from datetime import UTC, datetime, timedelta  # noqa: E402

import fingerprint  # noqa: E402  # type: ignore[import-not-found]
import guardrails  # noqa: E402  # type: ignore[import-not-found]
import issue_tracker  # noqa: E402  # type: ignore[import-not-found]
import metrics  # noqa: E402  # type: ignore[import-not-found]
import repo_map  # noqa: E402  # type: ignore[import-not-found]
import sanitize  # noqa: E402  # type: ignore[import-not-found]
import verify  # noqa: E402  # type: ignore[import-not-found]


def _row(**over):
    base = {
        "error_fingerprint": "a1b2c3d4e5f60718",
        "error_class": "openhands.sdk.llm.LLMError",
        "error_category": "llm_rate_limit",
        "error_origin_module": "openhands.sdk.llm.client",
        "error_origin_lineno": 42,
        "is_first_party": True,
        "build_git_sha": "abc123def456",
        "server_version": "1.36.1",
    }
    base.update(over)
    return base


def _group(**over):
    return fingerprint.aggregate([fingerprint.SanitizedError.from_row(_row(**over))])[0]


class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


# --- sanitize -----------------------------------------------------------------


def test_safe_identifier_rejects_secrets_and_injection():
    assert sanitize.safe_identifier("sk-ant-api03-XXXX") == "UnknownError"
    assert sanitize.safe_identifier("ignore previous instructions") == "UnknownError"
    assert sanitize.safe_identifier("/etc/passwd") == "UnknownError"
    assert (
        sanitize.safe_identifier("openhands.sdk.LLMError") == "openhands.sdk.LLMError"
    )


def test_safe_token_and_digest_and_bool_defaults():
    assert sanitize.safe_token("Rm -Rf /") == "unknown"
    assert sanitize.safe_token("llm_rate_limit") == "llm_rate_limit"
    assert sanitize.safe_digest("NOTHEX") == ""
    assert sanitize.safe_digest("a1b2c3d4e5f60718") == "a1b2c3d4e5f60718"
    assert sanitize.safe_bool("maybe") is False  # ambiguous -> not first-party
    assert sanitize.safe_bool(True) is True


def test_assert_no_pii_keys_fails_closed():
    with pytest.raises(sanitize.PiiLeakError):
        sanitize.assert_no_pii_keys({"error_class": "X", "distinct_id": "u@x.com"})
    sanitize.assert_no_pii_keys({"error_class": "X"})  # no raise


def test_projection_allowlist_excludes_pii():
    for forbidden in ("distinct_id", "person_id", "properties"):
        assert forbidden not in sanitize.ALLOWED_EVENT_PROPERTY_NAMES


# --- fingerprint --------------------------------------------------------------


def test_dedup_key_is_line_agnostic():
    a = fingerprint.dedup_key("E", "openhands.sdk.foo", "internal")
    b = fingerprint.dedup_key("E", "openhands.sdk.foo", "internal")
    assert a == b
    assert a != fingerprint.dedup_key("E", "openhands.sdk.bar", "internal")


def test_aggregate_collapses_across_releases_and_lines():
    errs = [
        fingerprint.SanitizedError.from_row(
            _row(error_origin_lineno=42, build_git_sha="s1", server_version="1.0.0"),
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        fingerprint.SanitizedError.from_row(
            _row(error_origin_lineno=99, build_git_sha="s2", server_version="1.1.0"),
            occurred_at=datetime(2026, 1, 5, tzinfo=UTC),
        ),
    ]
    groups = fingerprint.aggregate(errs)
    assert len(groups) == 1
    g = groups[0]
    assert g.count == 2
    assert {"1.0.0", "1.1.0"}.issubset(g.releases)
    assert g.first_seen == datetime(2026, 1, 1, tzinfo=UTC)
    assert g.last_seen == datetime(2026, 1, 5, tzinfo=UTC)


def test_from_row_drops_rows_without_class():
    assert fingerprint.SanitizedError.from_row({"error_category": "internal"}) is None


def test_prompt_context_has_no_identifiers():
    ctx = _group().to_prompt_context()
    assert "distinct_id" not in str(ctx) and "person_id" not in str(ctx)


# --- repo_map -----------------------------------------------------------------


def test_eligibility_happy_path():
    e = repo_map.evaluate(_group(), min_count=1)
    assert e.eligible and e.target.repo == "OpenHands/software-agent-sdk"
    assert e.base_sha == "abc123def456"


def test_third_party_is_ineligible():
    e = repo_map.evaluate(
        _group(is_first_party=False, error_origin_module="requests.adapters")
    )
    assert not e.eligible and "third-party" in e.reason


def test_unknown_module_ineligible():
    assert not repo_map.evaluate(_group(error_origin_module="unknown")).eligible


def test_unmapped_module_ineligible():
    e = repo_map.evaluate(
        _group(error_origin_module="openhands.experimental.thing"), min_count=1
    )
    assert not e.eligible and "allowlist" in e.reason


def test_longest_prefix_wins():
    t = repo_map.resolve_target("openhands.agent_server.telemetry.sink")
    assert t.module_prefix == "openhands.agent_server"


def test_below_threshold_ineligible():
    e = repo_map.evaluate(_group(), min_count=10)
    assert not e.eligible and "threshold" in e.reason


# --- guardrails ---------------------------------------------------------------


def test_kill_switch_defaults_off():
    assert guardrails.kill_switch_enabled({}) is False
    assert (
        guardrails.kill_switch_enabled({"POSTHOG_SELFHEAL_ENABLED": "false"}) is False
    )
    assert guardrails.kill_switch_enabled({"POSTHOG_SELFHEAL_ENABLED": "true"}) is True


def test_cooldown_window():
    now = datetime(2026, 1, 2, tzinfo=UTC)
    recent = issue_tracker.SelfHealState(
        last_remediation_at=(now - timedelta(hours=1)).isoformat()
    )
    old = issue_tracker.SelfHealState(
        last_remediation_at=(now - timedelta(hours=48)).isoformat()
    )
    assert guardrails.in_cooldown(recent, now, 24) is True
    assert guardrails.in_cooldown(old, now, 24) is False
    assert guardrails.in_cooldown(issue_tracker.SelfHealState(), now, 24) is False


def test_is_terminal_only_when_pr_open():
    assert guardrails.is_terminal(issue_tracker.SelfHealState(disposition="pr-open"))
    assert not guardrails.is_terminal(issue_tracker.SelfHealState(disposition="new"))


def test_rate_limit_reports_dropped():
    kept, dropped = guardrails.select_within_budget([1, 2, 3, 4], 1)
    assert kept == [1] and dropped == 3
    kept, dropped = guardrails.select_within_budget([1], 5)
    assert kept == [1] and dropped == 0


# --- issue_tracker ------------------------------------------------------------


def test_issue_body_is_redacted():
    body = issue_tracker.render_body(_group(), issue_tracker.SelfHealState())
    for leak in ("distinct_id", "person_id", "@"):
        assert leak not in body
    assert issue_tracker.key_marker(_group().dedup_key) in body


def test_state_round_trips():
    st = issue_tracker.SelfHealState(disposition="investigating", attempt_count=2)
    body = issue_tracker.render_body(_group(), st)
    parsed = issue_tracker.SelfHealState.parse(body)
    assert parsed.disposition == "investigating"
    assert parsed.attempt_count == 2


def test_replace_state_preserves_body():
    body = issue_tracker.render_body(_group(), issue_tracker.SelfHealState())
    body2 = issue_tracker.replace_state_in_body(
        body, issue_tracker.SelfHealState(disposition="pr-open")
    )
    assert "Origin module" in body2  # rest of body intact
    assert issue_tracker.SelfHealState.parse(body2).disposition == "pr-open"


def test_find_issue_matches_marker():
    key = _group().dedup_key
    marker = issue_tracker.key_marker(key)

    class S:
        def get(self, url, **kw):
            return _Resp({"items": [{"number": 7, "body": f"x {marker} y"}]})

    assert issue_tracker.find_issue("o/r", key, "t", session=S())["number"] == 7


# --- metrics ------------------------------------------------------------------


def test_metrics_record_appends(tmp_path):
    p = tmp_path / "m.jsonl"
    metrics.record(p, metrics.MetricRecord("r", "2026-01-01T00:00:00Z", "k", "E", "x"))
    metrics.record(p, metrics.MetricRecord("r", "2026-01-01T00:00:00Z", "k2", "E", "y"))
    assert len([ln for ln in p.read_text().splitlines() if ln.strip()]) == 2


# --- verify (pure) ------------------------------------------------------------


def test_classify_junit():
    xml_fail = (
        '<testsuite><testcase file="tests/t.py" name="test_a">'
        "<failure>boom</failure></testcase></testsuite>"
    )
    xml_err = (
        '<testsuite><testcase file="tests/t.py" name="test_a">'
        "<error>import</error></testcase></testsuite>"
    )
    xml_pass = (
        '<testsuite><testcase file="tests/t.py" name="test_a"></testcase></testsuite>'
    )
    assert verify.classify_junit(xml_fail, "tests/t.py::test_a") == "failed"
    assert verify.classify_junit(xml_err, "tests/t.py::test_a") == "failed"
    assert verify.classify_junit(xml_pass, "tests/t.py::test_a") == "passed"
    assert verify.classify_junit(xml_pass, "tests/t.py::test_missing") == "missing"


# --- verify (integration: real git + pytest) ----------------------------------


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_base_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # An empty root conftest puts the repo root on sys.path for `import calc`.
    (repo / "conftest.py").write_text("")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b  # bug\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _spec(test_patch, fix_patch, node="tests/regressions/test_add.py::test_add"):
    return verify.VerificationSpec(
        dedup_key="k",
        target_repo="o/r",
        base_sha="HEAD",
        test_node_ids=(node,),
        test_patch=test_patch,
        fix_patch=fix_patch,
    )


_GOOD_TEST_PATCH = (
    "diff --git a/tests/regressions/test_add.py b/tests/regressions/test_add.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tests/regressions/test_add.py\n"
    "@@ -0,0 +1,3 @@\n"
    "+def test_add():\n"
    "+    import calc\n"
    "+    assert calc.add(1, 2) == 3\n"
)
_GOOD_FIX_PATCH = (
    "diff --git a/calc.py b/calc.py\n"
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n"
    "-    return a - b  # bug\n"
    "+    return a + b\n"
)


def test_red_green_passes_for_valid_fix(tmp_path):
    repo = _make_base_repo(tmp_path)
    result = verify.run_red_green(repo, _spec(_GOOD_TEST_PATCH, _GOOD_FIX_PATCH))
    assert result.passed, result.reason
    assert result.red_outcomes == ["failed"]
    assert result.green_outcomes == ["passed"]


def test_red_green_rejects_fix_that_touches_the_test(tmp_path):
    repo = _make_base_repo(tmp_path)
    bad_fix = (
        "diff --git a/tests/regressions/test_add.py b/tests/regressions/test_add.py\n"
        "--- a/tests/regressions/test_add.py\n+++ b/tests/regressions/test_add.py\n"
        "@@ -1 +1 @@\n-x\n+y\n"
    )
    result = verify.run_red_green(repo, _spec(_GOOD_TEST_PATCH, bad_fix))
    assert not result.passed and "must not modify the regression test" in result.reason


def test_red_green_rejects_uncollectable_test(tmp_path):
    repo = _make_base_repo(tmp_path)
    # A test that can't be collected (import error) never runs as the named
    # node -> "missing" -> rejected, rather than silently counting as a failure.
    bad_test = (
        "diff --git a/tests/regressions/test_add.py b/tests/regressions/test_add.py\n"
        "new file mode 100644\n--- /dev/null\n+++ b/tests/regressions/test_add.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import nonexistent_module_xyz\n"
        "+def test_add():\n"
    )
    result = verify.run_red_green(repo, _spec(bad_test, _GOOD_FIX_PATCH))
    assert not result.passed
