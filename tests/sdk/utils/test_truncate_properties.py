"""Property-based tests for ``maybe_truncate``."""

from hypothesis import assume, given, strategies as st

from openhands.sdk.utils.truncate import DEFAULT_TRUNCATE_NOTICE, maybe_truncate


content = st.text(max_size=400)
# 0 is treated as "no limit" (``if not truncate_after``), so start at 1.
limit = st.integers(min_value=1, max_value=500)
notice = st.text(min_size=1, max_size=40) | st.just(DEFAULT_TRUNCATE_NOTICE)


@given(content, limit, notice)
def test_result_never_exceeds_the_limit(text, truncate_after, truncate_notice):
    result = maybe_truncate(text, truncate_after, truncate_notice)
    assert len(result) <= truncate_after


@given(content, limit, notice)
def test_short_content_is_returned_unchanged(text, truncate_after, truncate_notice):
    assume(len(text) <= truncate_after)
    assert maybe_truncate(text, truncate_after, truncate_notice) == text


# Multi-line output long enough to need truncation, and a limit below its
# length. Hypothesis favours short strings, so the size is drawn explicitly.
long_output = st.lists(
    st.text(st.characters(exclude_categories=("Cs",)), max_size=30),
    min_size=20,
    max_size=80,
).map("\n".join)


@st.composite
def output_and_limit(draw, minimum: int = 0):
    text = draw(long_output.filter(lambda t: len(t) > minimum + 1))
    return text, draw(st.integers(minimum + 1, len(text) - 1))


@given(st.data(), notice)
def test_truncated_result_is_head_notice_tail(data, truncate_notice):
    text, truncate_after = data.draw(output_and_limit(len(truncate_notice)))
    assume(truncate_notice not in text)
    result = maybe_truncate(text, truncate_after, truncate_notice)
    head, sep, tail = result.partition(truncate_notice)
    assert sep == truncate_notice
    assert text.startswith(head)
    assert text.endswith(tail)
    assert len(result) == truncate_after
