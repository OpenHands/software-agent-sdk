import asyncio
import contextlib
import json

import pytest
from litellm import ModelResponse, ModelResponseStream, Usage
from litellm.types.llms.openai import ResponseAPIUsage, ResponsesAPIResponse
from litellm.types.utils import Delta, StreamingChoices
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openhands.sdk import LLM, Message, TextContent


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


@pytest.fixture
def completion(monkeypatch, exported_spans):
    def invoke(*, asynchronous=False, streaming=False, missing_usage=False, fail=False):
        usage = (
            None
            if missing_usage
            else Usage(
                prompt_tokens=11,
                completion_tokens=3,
                total_tokens=14,
                prompt_tokens_details={"cached_tokens": 4, "cache_creation_tokens": 5},
            )
        )

        def response():
            value = ModelResponse(
                id="synthetic-response",
                model="claude-haiku-4-5-20251001",
                choices=[
                    {
                        "message": {
                            "role": "assistant",
                            "content": "RESPONSE_OK",
                            "reasoning_content": "PRIVATE_REASONING",
                        }
                    }
                ],
                usage=usage,
            )
            value._hidden_params = {
                "additional_headers": {"llm_provider-x-litellm-response-cost": "0.007"}
            }
            return value

        def chunks():
            yield ModelResponseStream(
                id="synthetic-response",
                model="claude-haiku-4-5-20251001",
                choices=[
                    StreamingChoices(
                        index=0, delta=Delta(role="assistant", content="RESPONSE_OK")
                    )
                ],
            )
            yield ModelResponseStream(
                id="synthetic-response",
                model="claude-haiku-4-5-20251001",
                choices=[
                    StreamingChoices(index=0, delta=Delta(), finish_reason="stop")
                ],
                usage=usage,
            )

        def transport(**kwargs):
            if fail:
                raise RuntimeError("synthetic provider failure")
            return chunks() if streaming else response()

        async def async_transport(**kwargs):
            if fail:
                raise RuntimeError("synthetic provider failure")
            if not streaming:
                return response()

            async def stream():
                for chunk in chunks():
                    yield chunk

            return stream()

        monkeypatch.setattr("openhands.sdk.llm.llm.litellm_completion", transport)
        monkeypatch.setattr(
            "openhands.sdk.llm.llm.litellm_acompletion", async_transport
        )
        llm = LLM(
            model="anthropic/claude-haiku-4-5-20251001",
            api_key="SYNTHETIC_TRANSPORT_CREDENTIAL",
            stream=streaming,
            max_output_tokens=512,
            reasoning_effort="none",
            extended_thinking_budget=None,
            num_retries=0,
        )
        messages = [
            Message(role="system", content=[TextContent(text="SYSTEM_OK")]),
            Message(role="user", content=[TextContent(text="REQUEST_OK")]),
        ]
        callbacks = []
        kwargs = {
            "on_token": callbacks.append if streaming else None,
            "extra_headers": {"Authorization": "SYNTHETIC_HEADER_CREDENTIAL"},
        }
        if asynchronous:
            result = asyncio.run(llm.acompletion(messages, **kwargs))
        else:
            result = llm.completion(messages, **kwargs)
        spans = exported_spans.get_finished_spans()
        assert len(spans) == 1
        return dict(spans[0].attributes or {}), result, callbacks

    return invoke


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("streaming", [False, True], ids=["complete", "stream"])
@pytest.mark.parametrize("field", ["input", "output", "usage"])
def test_completion_span_content_and_usage(completion, asynchronous, streaming, field):
    attrs, result, callbacks = completion(
        asynchronous=asynchronous, streaming=streaming
    )
    if field == "input":
        assert "REQUEST_OK" in attrs.get("gen_ai.input.messages", "")
        assert "SYSTEM_OK" in attrs["gen_ai.input.messages"]
        assert attrs["lmnr.span.input"] == attrs["gen_ai.input.messages"]
    elif field == "output":
        assert "RESPONSE_OK" in attrs.get("gen_ai.output.messages", "")
        assert attrs["lmnr.span.output"] == attrs["gen_ai.output.messages"]
        assert "PRIVATE_REASONING" not in attrs["gen_ai.output.messages"]
    else:
        assert attrs.get("gen_ai.usage.input_tokens") == 11
        assert attrs.get("gen_ai.usage.output_tokens") == 3
        assert attrs["gen_ai.usage.cache_read_input_tokens"] == 4
        assert attrs["gen_ai.usage.cache_creation_input_tokens"] == 5
        assert result.metrics.accumulated_token_usage.prompt_tokens == 11
    assert "SYNTHETIC_TRANSPORT_CREDENTIAL" not in json.dumps(attrs)
    assert "SYNTHETIC_HEADER_CREDENTIAL" not in json.dumps(attrs)
    if streaming:
        assert callbacks


def test_missing_usage_does_not_invent_token_counts(completion):
    attrs, _, _ = completion(asynchronous=True, missing_usage=True)
    assert "gen_ai.usage.input_tokens" not in attrs
    assert "gen_ai.usage.output_tokens" not in attrs
    assert "RESPONSE_OK" in attrs["gen_ai.output.messages"]


def test_provider_error_preserves_input_and_closes_span(completion, exported_spans):
    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        completion(asynchronous=True, fail=True)
    spans = exported_spans.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert "REQUEST_OK" in attrs["gen_ai.input.messages"]
    assert "gen_ai.output.messages" not in attrs


def test_content_annotation_failure_does_not_fail_completion(completion, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic serialization failure")

    monkeypatch.setattr(
        "openhands.sdk.llm.utils.telemetry.redact_text_secrets", unavailable
    )
    attrs, result, _ = completion(asynchronous=True)
    assert result.metrics.accumulated_token_usage.completion_tokens == 3
    assert attrs["gen_ai.usage.input_tokens"] == 11
    assert "gen_ai.output.messages" not in attrs


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_responses_span_content_without_completion_logging(
    monkeypatch, exported_spans, asynchronous
):
    response = ResponsesAPIResponse(
        id="synthetic-response",
        model="gpt-4o-mini",
        status="completed",
        created_at=1,
        output=[
            {
                "id": "message",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "RESPONSE_OK", "annotations": []}
                ],
            },
            {
                "id": "reasoning",
                "type": "reasoning",
                "summary": [],
                "encrypted_content": "PRIVATE_REASONING",
            },
        ],
        usage=ResponseAPIUsage(input_tokens=11, output_tokens=3, total_tokens=14),
    )

    def transport(**kwargs):
        return response

    async def async_transport(**kwargs):
        return response

    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_responses", transport)
    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_aresponses", async_transport)
    llm = LLM(
        model="openai/gpt-4o-mini",
        api_key="SYNTHETIC_TRANSPORT_CREDENTIAL",
        num_retries=0,
    )
    messages = [
        Message(role="system", content=[TextContent(text="SYSTEM_OK")]),
        Message(role="user", content=[TextContent(text="REQUEST_OK")]),
    ]
    if asynchronous:
        asyncio.run(llm.aresponses(messages))
    else:
        llm.responses(messages)
    spans = exported_spans.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert "SYSTEM_OK" in attrs["gen_ai.input.messages"]
    assert "REQUEST_OK" in attrs["gen_ai.input.messages"]
    assert "RESPONSE_OK" in attrs["gen_ai.output.messages"]
    assert "PRIVATE_REASONING" not in attrs["gen_ai.output.messages"]
    assert attrs["gen_ai.usage.input_tokens"] == 11
    assert attrs["gen_ai.usage.output_tokens"] == 3
