"""Untrusted-input validation for the self-healing workflow.

PostHog is an untrusted source even though the OSS-5715 telemetry feeding it is
sanitized at emission, so every value is re-validated here by *coercion*: a
surprising value degrades to a safe default rather than crashing the run or
escaping as free text into an issue, a log, or an agent prompt.

These primitives are a deliberate, self-contained copy of
``openhands.agent_server.telemetry.sanitizer`` (and the constrained scalars in
its ``models.py``), because the GitHub Actions job runs this example standalone
and cannot import the agent-server package.
``tests/cross/test_posthog_sanitizer_drift.py`` asserts the copy byte-matches.

Two rules carry the privacy guarantee:

1. **PII-bearing fields are never selected.** :data:`FORBIDDEN_PROPERTY_NAMES`
   names them and :func:`assert_no_pii_keys` fails closed if one appears.
2. **Only enum-ish tokens reach a prompt.** Every value in
   :meth:`fingerprint.FingerprintGroup.to_prompt_context` has passed a
   ``safe_*`` coercion, so no attacker-controlled string can carry an injected
   instruction into the agent.
"""

import re
from typing import Final


# --- Vendored regexes (must byte-match the agent-server sanitizer/models) -----
# Source of truth (asserted equal by the drift test):
#   sanitizer.py: _SAFE_TOKEN_RE, _SAFE_IDENTIFIER_RE, _SAFE_IDENTIFIER_MAX_LEN,
#                 _VERSION_RE, UNKNOWN_TOKEN, UNKNOWN_ERROR_CLASS
#   models.py:    Digest StringConstraints pattern
_SAFE_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.:\-]{0,63}$")
_SAFE_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*([.:][A-Za-z_][A-Za-z0-9_]*)*$"
)
_SAFE_IDENTIFIER_MAX_LEN: Final[int] = 96
_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.+\-/]{0,63}$"
)
_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{16,64}$")

UNKNOWN_TOKEN: Final[str] = "unknown"
UNKNOWN_ERROR_CLASS: Final[str] = "UnknownError"


def safe_token(value: object, *, default: str = UNKNOWN_TOKEN) -> str:
    """Coerce to a lowercase token, or ``default`` if it doesn't fit."""
    if not isinstance(value, str):
        return default
    candidate = value.strip().lower()
    return candidate if _SAFE_TOKEN_RE.match(candidate) else default


def safe_identifier(value: object, *, default: str = UNKNOWN_ERROR_CLASS) -> str:
    """Coerce to a dotted identifier, or ``default`` if it doesn't fit.

    Stricter than a token -- no dashes, spaces, slashes or ``@`` -- so neither an
    API key nor a filesystem path can occupy an ``error_class`` field.
    """
    if not isinstance(value, str):
        return default
    candidate = value.strip()
    if len(candidate) > _SAFE_IDENTIFIER_MAX_LEN:
        return default
    return candidate if _SAFE_IDENTIFIER_RE.match(candidate) else default


def safe_version(value: object, *, default: str = UNKNOWN_TOKEN) -> str:
    """Coerce a version / git ref / sha, or ``default`` if it doesn't fit."""
    if not isinstance(value, str):
        return default
    candidate = value.strip()
    return candidate if _VERSION_RE.match(candidate) else default


def safe_digest(value: object, *, default: str = "") -> str:
    """Coerce the emission-side fingerprint shape (16-64 hex chars), or ``default``.

    Anything else is treated as absent rather than trusted.
    """
    if not isinstance(value, str):
        return default
    candidate = value.strip().lower()
    return candidate if _DIGEST_RE.match(candidate) else default


def safe_lineno(value: object) -> int | None:
    """Coerce a non-negative line number, or ``None``."""
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def safe_bool(value: object) -> bool:
    """Coerce a strict boolean; anything ambiguous is ``False``.

    The safe default for ``is_first_party``: an unparseable value can never make
    a fingerprint look remediable.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


# --- PII boundary -------------------------------------------------------------
#: Columns that must never be selected from PostHog or retained: identity
#: fields, plus the raw ``properties`` blob that could embed a message, a
#: prompt, or a secret.
FORBIDDEN_PROPERTY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "distinct_id",
        "person_id",
        "$distinct_id",
        "properties",
        "elements_chain",
        "$ip",
        "$geoip_city_name",
    }
)

#: The only property columns the triage HogQL projection is allowed to select.
#: Mirrors the sanitized ``ErrorProperties`` + ``RuntimeProperties`` shapes.
ALLOWED_EVENT_PROPERTY_NAMES: Final[tuple[str, ...]] = (
    "error_fingerprint",
    "error_class",
    "error_category",
    "error_origin_module",
    "error_origin_lineno",
    "is_first_party",
    "is_terminal",
    "build_git_sha",
    "server_version",
    "sdk_version",
    "schema_version",
    "source",
)


class PiiLeakError(RuntimeError):
    """Raised when a forbidden identity column reaches a processing boundary."""


def assert_no_pii_keys(row: object) -> None:
    """Fail closed if a row carries a forbidden identity column.

    Defence in depth behind the projection allowlist: a hand-written query or a
    schema change must not be able to slip one through silently.
    """
    if isinstance(row, dict):
        leaked = FORBIDDEN_PROPERTY_NAMES.intersection(row.keys())
        if leaked:
            raise PiiLeakError(
                "forbidden identity column(s) present in event row: "
                + ", ".join(sorted(leaked))
            )
