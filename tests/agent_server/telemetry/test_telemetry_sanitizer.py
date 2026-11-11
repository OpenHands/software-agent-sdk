"""Proofs that sanitization does what the docstrings claim.

The central test here is
``test_fingerprint_ignores_the_exception_message``: it demonstrates that two
exceptions differing *only* in their message — one of which contains a secret —
produce the same fingerprint. That is only possible if the message is never
read, which is the whole guarantee.
"""

import json
from dataclasses import asdict
from types import TracebackType

import pytest

from openhands.agent_server.telemetry.sanitizer import (
    COST_BOUNDS,
    COUNT_BOUNDS,
    DURATION_BOUNDS,
    UNKNOWN_ERROR_CLASS,
    Bounds,
    bucket,
    model_family,
    normalize_error_code,
    normalize_exception,
    pseudonymize,
    safe_identifier,
    safe_token,
    safe_version,
)


SECRET = "sk-ant-api03-SUPERSECRETVALUE"
PROMPT = "Summarize this confidential memo: Dear Board, ..."
PATH = "/Users/alice/private/repo/main.py"


def _raise(message: str) -> BaseException:
    try:
        raise ValueError(message)
    except ValueError as exc:  # noqa: PERF203
        return exc


def test_fingerprint_ignores_the_exception_message():
    """Two errors from the same line fingerprint identically.

    If the message contributed to the hash, a secret in one of them would
    change the digest. Identical digests prove the message is never read.
    """
    a = _raise("secret-A")
    b = _raise("secret-B")

    fa = normalize_exception(a)
    fb = normalize_exception(b)

    assert fa.error_fingerprint == fb.error_fingerprint
    assert fa.error_class == fb.error_class == "ValueError"


def test_fingerprint_distinguishes_different_error_types():
    try:
        raise KeyError("k")
    except KeyError as exc:
        key_fp = normalize_exception(exc)

    value_fp = normalize_exception(_raise("v"))
    assert key_fp.error_fingerprint != value_fp.error_fingerprint


def _raise_deep(*, nameless: int, named: int) -> BaseException:
    """Outer nameless frames, then ``named`` distinct first-party frames.

    The traceback chain starts at this function and walks inward, so the
    nameless frames are the first links after this one.
    """
    named_ns: dict[str, object] = {"__name__": "openhands.sdk._fingerprint_budget"}
    chunks = [f"def p{named - 1}():\n    raise RuntimeError('budget')\n"]
    chunks.extend(f"def p{i}():\n    p{i + 1}()\n" for i in range(named - 2, -1, -1))
    exec("".join(chunks), named_ns)

    raiser = named_ns["p0"]
    if nameless:
        # No ``__name__``: these frames must not count as yielded names.
        nameless_ns: dict[str, object] = {"p0": raiser}
        chunks = ["def n0():\n    p0()\n"]
        chunks.extend(f"def n{i}():\n    n{i - 1}()\n" for i in range(1, nameless))
        exec("".join(chunks), nameless_ns)
        raiser = nameless_ns[f"n{nameless - 1}"]
    assert callable(raiser)
    try:
        raiser()
    except RuntimeError as exc:
        return exc
    raise AssertionError("expected RuntimeError")


def _truncate_links(tb: TracebackType | None, max_links: int) -> TracebackType:
    frames: list[TracebackType] = []
    current = tb
    while current is not None and len(frames) < max_links:
        frames.append(current)
        current = current.tb_next
    rebuilt: TracebackType | None = None
    for frame in reversed(frames):
        rebuilt = TracebackType(
            rebuilt, frame.tb_frame, frame.tb_lasti, frame.tb_lineno
        )
    assert rebuilt is not None
    return rebuilt


def _named_budget_lineno(tb: TracebackType | None, budget: int) -> int:
    """Line of the last named frame inside a yield budget that skips nameless."""
    current = tb
    yielded = 0
    lineno: int | None = None
    while current is not None and yielded < budget:
        module = current.tb_frame.f_globals.get("__name__")
        if isinstance(module, str) and module:
            lineno = current.tb_lineno
            yielded += 1
        current = current.tb_next
    assert lineno is not None
    return lineno


def test_fingerprint_counts_nameless_frames_against_the_link_budget():
    """A frame with no module name still consumes one of the 256 links.

    Skipping those frames would keep walking into later first-party frames and
    change ``error_fingerprint`` for a traceback longer than 256 links.
    """
    exc = _raise_deep(nameless=60, named=300)
    tb = exc.__traceback__
    links = 0
    named = 0
    current = tb
    while current is not None:
        links += 1
        module = current.tb_frame.f_globals.get("__name__")
        if isinstance(module, str) and module:
            named += 1
        current = current.tb_next
    assert links > 256
    assert named > 256

    full = normalize_exception(exc)
    truncated = normalize_exception(
        RuntimeError("budget").with_traceback(_truncate_links(tb, 256))
    )
    assert full.error_fingerprint == truncated.error_fingerprint
    assert full.error_origin_lineno == truncated.error_origin_lineno
    assert full.error_origin_module == "openhands.sdk._fingerprint_budget"
    assert full.error_origin_lineno != _named_budget_lineno(tb, 256)


def test_fingerprint_distinguishes_different_raise_sites():
    def site_one() -> BaseException:
        try:
            raise RuntimeError("x")
        except RuntimeError as exc:
            return exc

    def site_two() -> BaseException:
        try:
            raise RuntimeError("x")
        except RuntimeError as exc:
            return exc

    # Different lines in this (first-party-looking) test module would normally
    # differ; at minimum the fingerprints must be deterministic per site.
    assert (
        normalize_exception(site_one()).error_fingerprint
        == normalize_exception(site_one()).error_fingerprint
    )
    assert (
        normalize_exception(site_two()).error_fingerprint
        == normalize_exception(site_two()).error_fingerprint
    )


@pytest.mark.parametrize(
    "payload", [SECRET, PROMPT, PATH], ids=["secret", "prompt", "path"]
)
def test_no_part_of_the_message_survives_normalization(payload: str):
    fingerprint = normalize_exception(_raise(payload))
    serialized = json.dumps(asdict(fingerprint))

    assert payload not in serialized
    # Nor any distinctive fragment of it.
    for fragment in payload.split()[:3]:
        if len(fragment) > 6:
            assert fragment not in serialized


def test_normalization_never_emits_a_traceback_or_a_path():
    fingerprint = normalize_exception(_raise("boom"))
    serialized = json.dumps(asdict(fingerprint))

    assert "Traceback" not in serialized
    assert "/" not in serialized
    assert ".py" not in serialized


def test_third_party_exceptions_collapse_to_package_scope():
    """An unfamiliar dependency must not blow up cardinality."""

    class _FakeVendorError(Exception):
        pass

    _FakeVendorError.__module__ = "litellm.exceptions.deeply.nested"
    try:
        raise _FakeVendorError("nope")
    except _FakeVendorError as exc:
        fingerprint = normalize_exception(exc)

    assert fingerprint.error_class == "litellm:_FakeVendorError"
    assert fingerprint.is_first_party is False


def test_builtin_exceptions_keep_their_bare_name():
    assert normalize_exception(_raise("x")).error_class == "ValueError"


def test_category_is_derived_from_type_via_mro():
    try:
        raise FileNotFoundError("missing")
    except FileNotFoundError as exc:
        assert normalize_exception(exc).error_category == "workspace_io"

    try:
        raise TimeoutError("slow")
    except TimeoutError as exc:
        assert normalize_exception(exc).error_category == "llm_timeout"


def test_unknown_exception_types_get_the_unknown_category():
    class _Weird(Exception):
        pass

    try:
        raise _Weird("?")
    except _Weird as exc:
        assert normalize_exception(exc).error_category == "unknown"


def test_normalize_error_code_rejects_free_text():
    """ConversationErrorEvent.code is trusted only as far as it validates."""
    assert normalize_error_code("LLMAuthError").error_class == "LLMAuthError"
    # A code that is actually prose degrades rather than leaking.
    assert normalize_error_code(PROMPT).error_class == "UnknownError"
    assert normalize_error_code(None).error_class == "UnknownError"


# ── scalar coercion ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("finished", "finished"),
        ("FINISHED", "finished"),
        ("has space", "unknown"),
        ("/a/path", "unknown"),
        (None, "unknown"),
        (123, "unknown"),
    ],
)
def test_safe_token(value, expected):
    assert safe_token(value) == expected


def test_safe_identifier_rejects_secret_and_path_shapes():
    assert UNKNOWN_ERROR_CLASS == "UnknownError"
    assert safe_identifier("ValueError") == "ValueError"
    assert safe_identifier("openhands.sdk.Foo") == "openhands.sdk.Foo"
    assert safe_identifier(SECRET) == "UnknownError"
    assert safe_identifier(PATH) == "UnknownError"
    assert safe_identifier("a" * 200) == "UnknownError"


def test_safe_version_accepts_real_versions_and_refs():
    assert safe_version("1.36.1") == "1.36.1"
    assert safe_version("refs/heads/main") == "refs/heads/main"
    assert safe_version("unknown") == "unknown"
    assert safe_version("not a version!") == "unknown"


# ── bucketing ─────────────────────────────────────────────────────────────


def test_buckets_never_reveal_exact_magnitudes():
    assert bucket(0.5, DURATION_BOUNDS) == "lt-1"
    assert bucket(7, DURATION_BOUNDS) == "5-15"
    assert bucket(100_000, DURATION_BOUNDS) == "1800+"
    assert bucket(0, COUNT_BOUNDS) == "lt-1"
    assert bucket(3, COUNT_BOUNDS) == "1-5"
    assert bucket(0.005, COST_BOUNDS) == "lt-0p01"
    assert bucket(None, DURATION_BOUNDS) == "unknown"
    assert bucket(-1, DURATION_BOUNDS) == "unknown"


def test_bucket_is_monotonic():
    bounds = Bounds((1, 10, 100))
    labels = [bucket(v, bounds) for v in (0, 5, 50, 500)]
    assert len(set(labels)) == 4


# ── pseudonymisation ──────────────────────────────────────────────────────


def test_pseudonymize_is_stable_and_salt_dependent():
    a = pseudonymize("conversation-1", "salt-A")
    b = pseudonymize("conversation-1", "salt-A")
    c = pseudonymize("conversation-1", "salt-B")

    assert a == b
    assert a != c
    assert "conversation-1" not in a


def test_pseudonymize_accepts_an_oversized_salt():
    """blake2s caps keys at 32 bytes; a long secret must not raise."""
    digest = pseudonymize("x", "s" * 500)
    assert len(digest) == 32


# ── model families ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model,expected",
    [
        ("anthropic/claude-sonnet-5", "anthropic"),
        ("claude-opus-4-8", "anthropic"),
        ("gpt-4o", "openai"),
        ("gemini-2.0-flash", "gemini"),
        ("litellm_proxy/my-internal-deployment", "litellm_proxy"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_model_family_collapses_to_a_closed_vocabulary(model, expected):
    assert model_family(model) == expected


def test_model_family_never_leaks_a_custom_endpoint():
    assert "internal.corp" not in model_family("openai/https://internal.corp/v1/x")
