"""Span cost must be authoritative and cache-aware (issue #4817)."""

import contextlib

import pytest
from litellm.types.utils import ModelResponse, PromptTokensDetailsWrapper, Usage
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.llm.utils.telemetry import Telemetry


PROXY_COST = 0.084930


def _usage(cache_creation=20281, cache_read=0, prompt=20291, completion=83):
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=PromptTokensDetailsWrapper(cached_tokens=cache_read),
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )


def _response(usage, cost=PROXY_COST):
    resp = ModelResponse(model="litellm_proxy/claude-sonnet-4-5-20250929", usage=usage)
    resp._hidden_params = {
        "additional_headers": {"llm_provider-x-litellm-response-cost": cost}
    }
    return resp


@pytest.fixture
def exporter(monkeypatch):
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    tracer = provider.get_tracer("test")

    @contextlib.contextmanager
    def fake_span(name):
        with tracer.start_as_current_span(name) as span:
            yield span

    monkeypatch.setattr("openhands.sdk.observability.laminar.llm_call_span", fake_span)
    return exp


def _attrs(exporter):
    spans = exporter.get_finished_spans()
    assert len(spans) == 1, f"expected one span, got {len(spans)}"
    return dict(spans[0].attributes)


def test_span_cost_uses_authoritative_proxy_cost(exporter):
    t = Telemetry(
        model_name="litellm_proxy/claude-sonnet-4-5-20250929", metrics=Metrics()
    )
    t.on_request(telemetry_ctx={})
    t.on_response(_response(_usage()))

    attrs = _attrs(exporter)
    assert attrs["gen_ai.usage.cost"] == pytest.approx(PROXY_COST)
    # Not the flat token recompute that loses the cache buckets.
    flat = 20291 * 3e-6 + 83 * 15e-6
    assert attrs["gen_ai.usage.cost"] != pytest.approx(flat)


def test_span_carries_cache_buckets(exporter):
    t = Telemetry(model_name="m", metrics=Metrics())
    t.on_request(telemetry_ctx={})
    t.on_response(_response(_usage(cache_creation=20281, cache_read=7)))

    attrs = _attrs(exporter)
    assert attrs["gen_ai.usage.cache_creation_input_tokens"] == 20281
    assert attrs["gen_ai.usage.cache_read_input_tokens"] == 7


def test_span_cost_agrees_with_metrics(exporter):
    t = Telemetry(model_name="m", metrics=Metrics())
    t.on_request(telemetry_ctx={})
    t.on_response(_response(_usage()))

    attrs = _attrs(exporter)
    assert attrs["gen_ai.usage.cost"] == pytest.approx(t.metrics.accumulated_cost)


def test_cache_buckets_survive_absent_prompt_tokens_details():
    usage = Usage(prompt_tokens=100, completion_tokens=5)
    object.__setattr__(usage, "cache_creation_input_tokens", 42)
    assert Telemetry._cache_buckets(usage)[1] == 42


def test_span_closed_on_error(exporter):
    t = Telemetry(model_name="m", metrics=Metrics())
    t.on_request(telemetry_ctx={})
    t.on_error(RuntimeError("boom"))
    assert len(exporter.get_finished_spans()) == 1


def test_retry_does_not_leak_spans(exporter):
    t = Telemetry(model_name="m", metrics=Metrics())
    t.on_request(telemetry_ctx={})
    t.on_request(telemetry_ctx={})  # retry re-enters without a response
    t.on_response(_response(_usage()))
    assert len(exporter.get_finished_spans()) == 2
