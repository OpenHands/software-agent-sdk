"""Tests for the authoritative LLM span-cost rewrite (see #4816 / #4817)."""

from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import ReadableSpan

from openhands.sdk.llm.utils import telemetry as telemetry_mod
from openhands.sdk.llm.utils.telemetry import (
    _SPAN_COST_MAX_ENTRIES,
    consume_llm_span_cost,
    record_llm_span_cost,
)
from openhands.sdk.observability import span_cost_processor as processor_mod
from openhands.sdk.observability.span_cost_processor import (
    LLMSpanCostProcessor,
    install_llm_span_cost_processor,
)


COST_KEY = "gen_ai.usage.cost"
CACHE_READ_KEY = "gen_ai.usage.cache_read_input_tokens"
CACHE_WRITE_KEY = "gen_ai.usage.cache_creation_input_tokens"
RESPONSE_ID_KEY = "gen_ai.response.id"


@pytest.fixture(autouse=True)
def _clear_registry():
    """Isolate the module-level registry between tests."""
    with telemetry_mod._SPAN_COST_LOCK:
        telemetry_mod._SPAN_COST_BY_RESPONSE_ID.clear()
    yield
    with telemetry_mod._SPAN_COST_LOCK:
        telemetry_mod._SPAN_COST_BY_RESPONSE_ID.clear()


def _make_span(attributes) -> ReadableSpan:
    """Build a real ReadableSpan snapshot with the given attributes."""
    return ReadableSpan(name="litellm.completion", attributes=dict(attributes))


def test_record_and_consume_round_trip():
    cost = 0.001
    cache_read = 10
    cache_write = 20
    record_llm_span_cost("resp-1", cost, cache_read, cache_write)

    assert consume_llm_span_cost("resp-1") == (cost, cache_read, cache_write)
    # Popped once: a second consume returns nothing.
    assert consume_llm_span_cost("resp-1") is None


def test_record_empty_id_is_ignored():
    cost = 0.001
    zero = 0
    record_llm_span_cost("", cost, zero, zero)

    assert consume_llm_span_cost("") is None


def test_registry_is_bounded_and_evicts_oldest():
    zero_cost = 0.0
    zero = 0
    overflow = _SPAN_COST_MAX_ENTRIES + 5
    for i in range(overflow):
        record_llm_span_cost(f"r{i}", zero_cost, zero, zero)

    with telemetry_mod._SPAN_COST_LOCK:
        size = len(telemetry_mod._SPAN_COST_BY_RESPONSE_ID)
    assert size == _SPAN_COST_MAX_ENTRIES
    # The five oldest ids were evicted; the newest remain.
    assert consume_llm_span_cost("r0") is None
    assert consume_llm_span_cost(f"r{overflow - 1}") == (zero_cost, zero, zero)


def test_on_end_rewrites_cost_for_recorded_response():
    cost = 0.0042
    cache_read = 32
    zero = 0
    record_llm_span_cost("resp-2", cost, cache_read, zero)
    span = _make_span({RESPONSE_ID_KEY: "resp-2"})

    LLMSpanCostProcessor().on_end(span)

    attrs = dict(span.attributes or {})
    assert attrs[COST_KEY] == cost
    assert attrs[CACHE_READ_KEY] == cache_read
    assert attrs[CACHE_WRITE_KEY] == zero


def test_on_end_noops_without_recorded_entry():
    span = _make_span({RESPONSE_ID_KEY: "resp-3"})

    LLMSpanCostProcessor().on_end(span)

    assert COST_KEY not in dict(span.attributes or {})


def test_on_end_ignores_span_without_response_id():
    span = _make_span({})

    LLMSpanCostProcessor().on_end(span)

    assert COST_KEY not in dict(span.attributes or {})


def test_zero_cost_is_not_written_but_cache_buckets_are():
    zero_cost = 0.0
    cache_read = 7
    cache_write = 3
    record_llm_span_cost("resp-4", zero_cost, cache_read, cache_write)
    span = _make_span({RESPONSE_ID_KEY: "resp-4"})

    LLMSpanCostProcessor().on_end(span)

    attrs = dict(span.attributes or {})
    assert COST_KEY not in attrs
    assert attrs[CACHE_READ_KEY] == cache_read
    assert attrs[CACHE_WRITE_KEY] == cache_write


def test_install_prepends_processor_ahead_of_existing():
    existing = MagicMock()
    active = MagicMock()
    active._span_processors = (existing,)
    active._lock = MagicMock()
    active._lock.__enter__ = MagicMock(return_value=None)
    active._lock.__exit__ = MagicMock(return_value=False)
    provider = MagicMock()
    provider._active_span_processor = active
    wrapper = MagicMock()
    wrapper.instance._tracer_provider = provider

    processor_mod._installed = False
    tracing_mod = MagicMock()
    tracing_mod.TracerWrapper = wrapper
    with patch.dict("sys.modules", {"lmnr.opentelemetry_lib.tracing": tracing_mod}):
        install_llm_span_cost_processor()

    processors = active._span_processors
    assert isinstance(processors[0], LLMSpanCostProcessor)
    assert processors[1] is existing
    assert processor_mod._installed is True
    processor_mod._installed = False
