"""Reduce rich runtime values to the narrow, non-identifying shapes in
:mod:`.models`.

Two rules carry the privacy guarantee, and both are testable:

1. **The exception message is never read.** ``str(exc)`` does not appear on any
   path in this module. Messages routinely embed prompts, file paths, request
   bodies and API keys, so the only safe policy is to never look.

2. **The traceback is never formatted.** We walk ``exc.__traceback__``
   manually, reading only ``tb_frame.f_globals["__name__"]`` and
   ``tb.tb_lineno``. ``traceback.extract_tb`` / ``format_exception`` are
   forbidden here because :class:`traceback.FrameSummary` reads the actual
   *source line* off disk into the object, and ``f_locals`` obviously holds
   live values.

The fingerprint is a grouping key, not a redaction mechanism: its preimage is
already PII-free, so the hash is defence in depth rather than the thing being
relied on.
"""

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from types import TracebackType
from typing import Final

from openhands.agent_server.telemetry.models import (
    ERROR_CATEGORY_BY_CLASS_NAME,
    TELEMETRY_SCHEMA_VERSION,
    ErrorCategory,
)


_FIRST_PARTY_ROOT: Final = "openhands"
_MAX_FINGERPRINT_FRAMES: Final = 5

_SAFE_TOKEN_RE: Final = re.compile(r"^[a-z0-9][a-z0-9_.:\-]{0,63}$")
_SAFE_IDENTIFIER_RE: Final = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*([.:][A-Za-z_][A-Za-z0-9_]*)*$"
)
_SAFE_IDENTIFIER_MAX_LEN: Final = 96
_VERSION_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+\-/]{0,63}$")

UNKNOWN_TOKEN: Final = "unknown"
UNKNOWN_ERROR_CLASS: Final = "UnknownError"
_UNKNOWN_CATEGORY: Final[ErrorCategory] = "unknown"


def safe_token(value: object, *, default: str = UNKNOWN_TOKEN) -> str:
    """Coerce to a lowercase token, or ``default`` if it doesn't fit.

    Coerce rather than raise: a surprising value should degrade the event, not
    lose it or crash a conversation.
    """
    if not isinstance(value, str):
        return default
    candidate = value.strip().lower()
    return candidate if _SAFE_TOKEN_RE.match(candidate) else default


def safe_identifier(value: object, *, default: str = UNKNOWN_ERROR_CLASS) -> str:
    """Coerce to a dotted identifier, or ``default`` if it doesn't fit."""
    if not isinstance(value, str):
        return default
    candidate = value.strip()
    if len(candidate) > _SAFE_IDENTIFIER_MAX_LEN:
        return default
    return candidate if _SAFE_IDENTIFIER_RE.match(candidate) else default


def safe_version(value: object, *, default: str = UNKNOWN_TOKEN) -> str:
    """Coerce a version / git ref, or ``default`` if it doesn't fit."""
    if not isinstance(value, str):
        return default
    candidate = value.strip()
    return candidate if _VERSION_RE.match(candidate) else default


def allowlisted(
    value: object, allowed: frozenset[str], *, default: str = "other"
) -> str:
    """Map ``value`` onto a closed vocabulary, collapsing anything else.

    Bounds cardinality and guarantees no free-form string escapes.
    """
    token = safe_token(value, default=default)
    return token if token in allowed else default


DURATION_BOUNDS: Final[tuple[float, ...]] = (1, 5, 15, 60, 300, 1800)
COUNT_BOUNDS: Final[tuple[float, ...]] = (1, 5, 20, 100, 500)
TOKEN_BOUNDS: Final[tuple[float, ...]] = (1_000, 10_000, 50_000, 200_000, 1_000_000)
COST_BOUNDS: Final[tuple[float, ...]] = (0.01, 0.1, 1, 10, 100)


def bucket(value: float | None, bounds: tuple[float, ...]) -> str:
    """Map a magnitude to a coarse label such as ``5-20`` or ``500+``.

    Raw magnitudes are a re-identification vector once joined with a
    timestamp, so no count, duration or cost is ever reported exactly.
    """
    if value is None:
        return UNKNOWN_TOKEN
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return UNKNOWN_TOKEN
    if numeric < 0:
        return UNKNOWN_TOKEN

    previous: float | None = None
    for upper in bounds:
        if numeric < upper:
            return _format_bucket(previous, upper)
        previous = upper
    return f"{_format_number(bounds[-1])}+"


def _format_bucket(lower: float | None, upper: float) -> str:
    if lower is None:
        return f"lt-{_format_number(upper)}"
    return f"{_format_number(lower)}-{_format_number(upper)}"


def _format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value).replace(".", "p")


def _derive_key(salt: str | bytes | None) -> bytes:
    """Normalise any salt to the 32 bytes blake2s accepts as a key."""
    if salt is None:
        return b""
    raw = salt.encode("utf-8") if isinstance(salt, str) else salt
    if len(raw) <= 32:
        return raw
    return hashlib.blake2s(raw, digest_size=32).digest()


def pseudonymize(value: str | bytes, salt: str | bytes | None) -> str:
    """Keyed digest of an identifier.

    Used for conversation ids, which appear in URLs and logs — shipping one
    raw would make the whole analytics dataset joinable back to a person.
    """
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.blake2s(raw, key=_derive_key(salt), digest_size=16).hexdigest()


@lru_cache(maxsize=1)
def _known_providers() -> frozenset[str]:
    """Provider names, sourced from litellm rather than hand-maintained."""
    from litellm import provider_list

    return frozenset(str(getattr(p, "value", p)).lower() for p in provider_list)


_MODEL_FAMILY_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("gemini", "gemini"),
    ("mistral", "mistral"),
    ("deepseek", "deepseek"),
    ("llama", "ollama"),
    ("grok", "xai"),
)


def model_family(model: object) -> str:
    """Reduce a model string to a coarse provider family.

    ``litellm`` model strings can carry deployment names and, for custom
    endpoints, hostnames — so only the recognised family is ever reported.
    """
    if not isinstance(model, str) or not model.strip():
        return UNKNOWN_TOKEN
    lowered = model.strip().lower()

    prefix = lowered.split("/", 1)[0] if "/" in lowered else ""
    if prefix in _known_providers():
        return prefix

    for hint, family in _MODEL_FAMILY_HINTS:
        if hint in lowered:
            return family
    return "other"


@dataclass(frozen=True, slots=True)
class ErrorFingerprint:
    """The complete, sanitized description of a failure."""

    error_class: str
    error_category: ErrorCategory
    error_fingerprint: str
    error_origin_module: str | None
    error_origin_lineno: int | None
    is_first_party: bool


def _walk_frames(tb: TracebackType | None) -> Iterator[tuple[str, int]]:
    """Collect ``(module, lineno)`` pairs without materialising source.

    Reads only ``f_globals["__name__"]`` and ``tb_lineno``. Never touches
    ``f_locals``, and never calls into :mod:`traceback`.
    """
    current = tb
    # Bounded: a runaway recursion produces a huge chain.
    remaining = 256
    while current is not None and remaining > 0:
        module = current.tb_frame.f_globals.get("__name__")
        if isinstance(module, str) and module:
            yield module, current.tb_lineno
        current = current.tb_next
        remaining -= 1


def _classify(exc: BaseException) -> tuple[str, bool]:
    """Return ``(error_class, is_first_party)``.

    First-party keeps its dotted module for precision. Third-party collapses
    to ``pkg:QualName`` so an unfamiliar dependency cannot blow up cardinality.
    """
    exc_type = type(exc)
    # __name__, not __qualname__: a nested class gives ``outer.<locals>.Inner``,
    # which is not a valid identifier and would bypass validation.
    name = safe_identifier(getattr(exc_type, "__name__", None))
    module = getattr(exc_type, "__module__", "") or ""

    if module in ("builtins", "", "__main__"):
        return name, False

    if module == _FIRST_PARTY_ROOT or module.startswith(f"{_FIRST_PARTY_ROOT}."):
        return safe_identifier(f"{module}.{name}", default=name), True

    top_level = module.split(".", 1)[0]
    return safe_identifier(f"{top_level}:{name}", default=name), False


def _categorize(exc: BaseException) -> ErrorCategory:
    """Category from the exception *type* only, walking the MRO."""
    candidates: Iterator[ErrorCategory | None] = (
        ERROR_CATEGORY_BY_CLASS_NAME.get(klass.__name__) for klass in type(exc).__mro__
    )
    # Consumed with a loop rather than next(): pyright widens next() over a
    # generator expression to str, losing the Literal type.
    for category in candidates:
        if category is not None:
            return category
    return _UNKNOWN_CATEGORY


def normalize_exception(exc: BaseException) -> ErrorFingerprint:
    """Reduce an exception to a groupable, non-identifying fingerprint.

    The message is never read and the traceback is never formatted; see the
    module docstring.
    """
    error_class, is_first_party = _classify(exc)
    category = _categorize(exc)

    frames = list(_walk_frames(exc.__traceback__))
    first_party_frames = [
        (module, lineno)
        for module, lineno in frames
        if module == _FIRST_PARTY_ROOT or module.startswith(f"{_FIRST_PARTY_ROOT}.")
    ]

    origin_module: str | None = None
    origin_lineno: int | None = None
    if first_party_frames:
        deepest_module, deepest_lineno = first_party_frames[-1]
        origin_module = safe_identifier(deepest_module, default=UNKNOWN_TOKEN)
        origin_lineno = deepest_lineno
    elif frames:
        # No first-party frame: report only the top-level package.
        deepest_module, deepest_lineno = frames[-1]
        origin_module = safe_identifier(
            deepest_module.split(".", 1)[0], default=UNKNOWN_TOKEN
        )
        origin_lineno = deepest_lineno

    # Preimage is already PII-free: class, category, module/line. No message.
    parts = [str(TELEMETRY_SCHEMA_VERSION), error_class, category]
    parts.extend(
        f"{module}:{lineno}"
        for module, lineno in first_party_frames[-_MAX_FINGERPRINT_FRAMES:]
    )
    fingerprint = hashlib.blake2s(
        "|".join(parts).encode("utf-8"), digest_size=8
    ).hexdigest()

    return ErrorFingerprint(
        error_class=error_class,
        error_category=category,
        error_fingerprint=fingerprint,
        error_origin_module=origin_module,
        error_origin_lineno=origin_lineno,
        is_first_party=is_first_party,
    )


def normalize_error_code(code: object) -> ErrorFingerprint:
    """Fingerprint a failure known only by a string code.

    ``ConversationErrorEvent.code`` is documented as "typically a type", so it
    is treated as a class name and run through the same validation. The
    accompanying ``detail`` field is never read.
    """
    error_class = safe_identifier(code)
    category = ERROR_CATEGORY_BY_CLASS_NAME.get(
        error_class.rsplit(".", 1)[-1].rsplit(":", 1)[-1], "unknown"
    )
    fingerprint = hashlib.blake2s(
        "|".join([str(TELEMETRY_SCHEMA_VERSION), error_class, category]).encode(
            "utf-8"
        ),
        digest_size=8,
    ).hexdigest()
    return ErrorFingerprint(
        error_class=error_class,
        error_category=category,
        error_fingerprint=fingerprint,
        error_origin_module=None,
        error_origin_lineno=None,
        is_first_party=False,
    )
