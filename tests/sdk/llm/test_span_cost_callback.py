"""Tests for LLM._add_span_cost_callback wiring (see #4816 / #4817)."""

import asyncio

import pytest

from openhands.sdk.llm import LLM
from openhands.sdk.llm.utils import telemetry as telemetry_mod
from openhands.sdk.llm.utils.telemetry import consume_llm_span_cost


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


def test_sync_callback_records_cost(llm):
    kwargs: dict = {}
    llm._add_span_cost_callback(kwargs, async_=False)

    callbacks = kwargs["success_callback"]
    assert callbacks, "expected a success callback to be attached"

    recorder = callbacks[-1]
    recorder({}, _FakeResponse("resp-sync"))

    entry = consume_llm_span_cost("resp-sync")
    assert entry is not None
    cost, cache_read, cache_write = entry
    assert cache_read == 0
    assert cache_write == 0


def test_async_callback_records_cost(llm):
    kwargs: dict = {}
    llm._add_span_cost_callback(kwargs, async_=True)

    recorder = kwargs["success_callback"][-1]
    assert asyncio.iscoroutinefunction(recorder)

    asyncio.run(recorder({}, _FakeResponse("resp-async"), None, None))

    assert consume_llm_span_cost("resp-async") is not None


def test_existing_callbacks_are_preserved(llm):
    def sentinel(*_args, **_kwargs):
        return None

    kwargs = {"success_callback": [sentinel]}
    llm._add_span_cost_callback(kwargs, async_=False)

    assert sentinel in kwargs["success_callback"]
    assert len(kwargs["success_callback"]) == 2


def test_callback_ignores_response_without_id(llm):
    kwargs: dict = {}
    llm._add_span_cost_callback(kwargs, async_=False)

    recorder = kwargs["success_callback"][-1]
    recorder({}, _FakeResponse(None))

    assert consume_llm_span_cost("") is None
