"""Regression tests for independence between local completion-JSON logging
(``LLM.log_completions`` / ``Telemetry.log_enabled``) and OpenTelemetry span
content/metrics (gated by whether an exporter is configured, not by
``log_completions``).

See PR #6573 discussion: ``log_enabled`` originates from V0's local
completion-file logging and must not be read as a tracing opt-in/opt-out.
"""

import contextlib
import json
import os
import tempfile

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openhands.sdk import LLM, Message, TextContent
from tests.conftest import create_mock_litellm_response


@pytest.fixture
def exported_spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    @contextlib.contextmanager
    def llm_span(name):
        with tracer.start_as_current_span(name) as span:
            yield span

    monkeypatch.setattr("openhands.sdk.observability.laminar.llm_call_span", llm_span)
    yield exporter
    provider.shutdown()


def _run_completion(monkeypatch, *, log_completions: bool, log_dir: str | None):
    def completion(**kwargs):
        response = create_mock_litellm_response(content="RESPONSE_OK")
        response._hidden_params = {
            "additional_headers": {"llm_provider-x-litellm-response-cost": "0.007"}
        }
        return response

    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_completion",
        completion,
    )
    kwargs = {}
    if log_completions:
        assert log_dir is not None
        kwargs["log_completions_folder"] = log_dir
    llm = LLM(
        model="openai/gpt-4o-mini",
        api_key="SYNTHETIC_TRANSPORT_CREDENTIAL",
        log_completions=log_completions,
        num_retries=0,
        **kwargs,
    )
    messages = [
        Message(role="system", content=[TextContent(text="SYSTEM_OK")]),
        Message(role="user", content=[TextContent(text="REQUEST_OK")]),
    ]
    result = llm.completion(messages)
    return result


@pytest.mark.parametrize("log_completions", [False, True], ids=["log_off", "log_on"])
@pytest.mark.parametrize(
    "tracing_enabled", [False, True], ids=["trace_off", "trace_on"]
)
def test_completion_logging_and_tracing_are_independent(
    monkeypatch, exported_spans, log_completions, tracing_enabled
):
    """Exercise independent log and span outputs with an in-memory exporter."""
    if not tracing_enabled:
        monkeypatch.setattr(
            "openhands.sdk.observability.laminar.llm_call_span",
            lambda name: contextlib.nullcontext(None),
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        result = _run_completion(
            monkeypatch, log_completions=log_completions, log_dir=temp_dir
        )

        spans = exported_spans.get_finished_spans()
        if tracing_enabled:
            assert len(spans) == 1
            attrs = dict(spans[0].attributes or {})
            assert "REQUEST_OK" in attrs["gen_ai.input.messages"]
            assert "RESPONSE_OK" in attrs["gen_ai.output.messages"]
            assert attrs["gen_ai.usage.input_tokens"] == 10
            assert attrs["gen_ai.usage.output_tokens"] == 5
            assert attrs["gen_ai.usage.cost"] == pytest.approx(0.007)
            assert attrs["lmnr.span.input"] == attrs["gen_ai.input.messages"]
            assert attrs["lmnr.span.output"] == attrs["gen_ai.output.messages"]
        else:
            assert len(spans) == 0

        # SDK-level metrics should always reflect usage/cost, independent of
        # tracing being enabled.
        usage = result.metrics.accumulated_token_usage
        assert usage is not None
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 5
        assert result.metrics.accumulated_cost == pytest.approx(0.007)

        log_files = os.listdir(temp_dir)
        if log_completions:
            assert len(log_files) == 1
            with open(os.path.join(temp_dir, log_files[0])) as f:
                log_data = json.loads(f.read())
            assert "response" in log_data
        else:
            assert log_files == []


def test_log_completions_default_false_omitted_is_compatible(monkeypatch, tmp_path):
    """Compatibility check: omitting log_completions (default False) behaves
    the same as explicitly passing False, and does not require a tracer to be
    configured.
    """
    monkeypatch.setattr(
        "openhands.sdk.observability.laminar.should_enable_observability",
        lambda: False,
    )
    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_completion",
        lambda **kwargs: create_mock_litellm_response(content="RESPONSE_OK"),
    )
    llm = LLM(
        model="openai/gpt-4o-mini",
        api_key="SYNTHETIC_TRANSPORT_CREDENTIAL",
        log_completions_folder=str(tmp_path),
        num_retries=0,
    )
    assert llm.log_completions is False
    messages = [
        Message(role="system", content=[TextContent(text="SYSTEM_OK")]),
        Message(role="user", content=[TextContent(text="REQUEST_OK")]),
    ]
    result = llm.completion(messages)
    usage = result.metrics.accumulated_token_usage
    assert usage is not None
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 5
    assert list(tmp_path.iterdir()) == []


def test_log_completions_true_without_tracer_only_writes_local_log(monkeypatch):
    """With a real, disabled call-span (no tracer configured), enabling
    log_completions should still write the local JSON log without requiring
    any OTel setup.
    """
    monkeypatch.setattr(
        "openhands.sdk.observability.laminar.should_enable_observability",
        lambda: False,
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        _run_completion(monkeypatch, log_completions=True, log_dir=temp_dir)
        log_files = os.listdir(temp_dir)
        assert len(log_files) == 1
