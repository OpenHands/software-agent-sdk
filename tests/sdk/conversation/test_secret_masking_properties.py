"""Property-based tests for secret masking in tool output.

Secrets are drawn from uppercase letters and the surrounding output from
lowercase letters and digits. The mask token ``<secret-hidden>`` has no
uppercase letters, so every uppercase letter left in the output is a piece
of the original text that was not masked.
"""

import pytest
from hypothesis import example, given, strategies as st

from openhands.sdk.conversation.secret_registry import SecretRegistry


secret_value = st.text("ABC", min_size=2, max_size=6)
secrets = st.lists(secret_value, min_size=1, max_size=4, unique=True)
filler = st.text("xy0 ", max_size=4)


@st.composite
def output_with_secrets(draw):
    values = draw(secrets)
    pieces = draw(st.lists(st.sampled_from(values) | filler, max_size=8))
    return values, "".join(pieces)


def _registry(values: list[str]) -> SecretRegistry:
    registry = SecretRegistry()
    registry.update_secrets({f"S{i}": v for i, v in enumerate(values)})
    for name in registry.secret_sources:
        registry.get_secret_value(name)
    return registry


def _covered(text: str, values: list[str]) -> set[int]:
    """Positions of ``text`` inside at least one occurrence of a secret."""
    covered = set()
    for value in values:
        start = text.find(value)
        while start != -1:
            covered.update(range(start, start + len(value)))
            start = text.find(value, start + 1)
    return covered


def _leaked_letters(original: str, masked: str, values: list[str]) -> int:
    """Uppercase letters of secret occurrences still present after masking."""
    uncovered = sum(
        1
        for i, ch in enumerate(original)
        if ch.isupper() and i not in _covered(original, values)
    )
    return sum(ch.isupper() for ch in masked) - uncovered


@given(output_with_secrets())
def test_masked_output_contains_no_secret(case):
    values, text = case
    masked = _registry(values).mask_secrets_in_output(text)
    assert not any(v in masked for v in values)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "values are replaced one after another, so two overlapping occurrences "
        "leave the part the first replacement did not cover in cleartext"
    ),
)
@given(output_with_secrets())
@example((["AAA", "AAAA"], "AAAAAA"))
def test_masked_output_keeps_no_fragment_of_a_secret(case):
    values, text = case
    masked = _registry(values).mask_secrets_in_output(text)
    assert _leaked_letters(text, masked, values) == 0, masked


@st.composite
def chunked(draw, text: str):
    cuts = sorted(draw(st.sets(st.integers(0, len(text)), max_size=6)))
    bounds = [0, *cuts, len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


@given(output_with_secrets(), st.data())
def test_stream_mask_output_does_not_depend_on_chunking(case, data):
    values, text = case
    registry = _registry(values)
    whole = registry.compile_stream_mask()
    expected = whole.feed(text) + whole.flush()

    stream = registry.compile_stream_mask()
    chunks = data.draw(chunked(text))
    assert "".join(stream.feed(c) for c in chunks) + stream.flush() == expected


@given(output_with_secrets(), st.data())
def test_stream_mask_hides_every_secret(case, data):
    values, text = case
    stream = _registry(values).compile_stream_mask()
    chunks = data.draw(chunked(text))
    masked = "".join(stream.feed(c) for c in chunks) + stream.flush()
    assert not any(v in masked for v in values), masked
