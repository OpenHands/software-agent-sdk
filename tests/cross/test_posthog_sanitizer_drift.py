"""Guard the vendored sanitizer copy against drift.

The example's ``sanitize.py`` is a deliberate, self-contained copy of the
validation primitives in ``openhands.agent_server.telemetry.sanitizer`` /
``models``, because its GitHub Actions job runs standalone and cannot import the
agent-server package.

The PII guarantee only holds if the copy stays faithful, so these tests assert
the *pattern strings* and key constants byte-match the source.
"""

import sys
from pathlib import Path


_EXAMPLE = (
    Path(__file__).parent.parent.parent
    / "examples"
    / "03_github_workflows"
    / "05_posthog_debugging"
)
sys.path.insert(0, str(_EXAMPLE))

import sanitize as vendored  # noqa: E402  # type: ignore[import-not-found]

from openhands.agent_server.telemetry import (  # noqa: E402
    models as source_models,
    sanitizer as source,
)


def test_token_regex_matches():
    assert vendored._SAFE_TOKEN_RE.pattern == source._SAFE_TOKEN_RE.pattern


def test_identifier_regex_and_length_match():
    assert vendored._SAFE_IDENTIFIER_RE.pattern == source._SAFE_IDENTIFIER_RE.pattern
    assert vendored._SAFE_IDENTIFIER_MAX_LEN == source._SAFE_IDENTIFIER_MAX_LEN


def test_version_regex_matches():
    assert vendored._VERSION_RE.pattern == source._VERSION_RE.pattern


def test_unknown_constants_match():
    assert vendored.UNKNOWN_TOKEN == source.UNKNOWN_TOKEN
    assert vendored.UNKNOWN_ERROR_CLASS == source.UNKNOWN_ERROR_CLASS


def _digest_pattern(annotated) -> str:
    for meta in getattr(annotated, "__metadata__", ()):
        pattern = getattr(meta, "pattern", None)
        if pattern:
            return pattern
    raise AssertionError("could not extract Digest pattern from source models")


def test_digest_regex_matches_source_models():
    assert vendored._DIGEST_RE.pattern == _digest_pattern(source_models.Digest)
