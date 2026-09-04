"""Tests for the global litellm span-cost callback (see #4816 / #4817).

The cost is published from litellm's global ``callbacks`` (a ``CustomLogger``
that fires reliably for sync and async completions with a stable 4-argument
signature), then consumed by ``LLMSpanCostProcessor``. Older revisions used a
per-request ``success_callback`` kwarg, which litellm only dispatched when a
global callback was already configured and always invoked with 4 positional
args regardless of the callback's arity.
"""

import asyncio

import litellm
import pytest

from openhands.sdk.llm import LLM
from openhands.sdk.llm.utils import telemetry as telemetry_mod
from openhands.sdk.llm.utils.telemetry import (
    consume_llm_span_cost,
    install_llm_cost_callback,
)


class _FakeUsage:
    prompt_tokens = 100
    completion_tokens = 50
    total_tokens = 150


class _FakeResponse:
    def __init__(self, response_id):
        self.id = response_id
        self.usage = _FakeUsage()
        self.model = "gpt-4o"


@pytest.fixture
def llm():
    return LLM(model="gpt-4o", api_key=None, usage_id="test-span-cost")


@pytest.fixture(autouse=True)
def _clear_registry():
    with telemetry_mod._SPAN_COST_LOCK:
        telemetry_mod._SPAN_COST_BY_RESPONSE_ID.clear()
    yield
    with telemetry_mod._SPAN_COST_LOCK:
        telemetry_mod._SPAN_COST_BY_RESPONSE_ID.clear()


def _installed_callback():
    for cb in reversed(litellm.callbacks):
        if type(cb).__name__ == "_LLMSpanCostCallback":
            return cb
    return None


def test_install_registers_global_callback():
    install_llm_cost_callback()
    assert _installed_callback() is not None
    # Idempotent: a second install does not append a duplicate.
    before = sum(
        1 for cb in litellm.callbacks if type(cb).__name__ == "_LLMSpanCostCallback"
    )
    install_llm_cost_callback()
    after = sum(
        1 for cb in litellm.callbacks if type(cb).__name__ == "_LLMSpanCostCallback"
    )
    assert before == after == 1


def test_llm_wiring_installs_callback(llm):
    llm._ensure_span_cost_callback()
    assert _installed_callback() is not None


def test_sync_success_event_records_cost():
    install_llm_cost_callback()
    cb = _installed_callback()
    # litellm passes 4 positional args; response_cost is its authoritative value.
    cb.log_success_event(
        {"response_cost": 0.0012}, _FakeResponse("resp-sync"), None, None
    )

    entry = consume_llm_span_cost("resp-sync")
    assert entry is not None
    cost, cache_read, cache_write = entry
    assert cost == pytest.approx(0.0012)
    assert cache_read == 0
    assert cache_write == 0


def test_async_success_event_records_cost():
    install_llm_cost_callback()
    cb = _installed_callback()
    asyncio.run(
        cb.async_log_success_event(
            {"response_cost": 0.0034}, _FakeResponse("resp-async"), None, None
        )
    )

    entry = consume_llm_span_cost("resp-async")
    assert entry is not None
    assert entry[0] == pytest.approx(0.0034)


def test_success_event_ignores_response_without_id():
    install_llm_cost_callback()
    cb = _installed_callback()
    cb.log_success_event({"response_cost": 0.0012}, _FakeResponse(None), None, None)

    assert consume_llm_span_cost("") is None
