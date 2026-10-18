import json
import threading
from collections import deque
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from openhands.sdk.llm import (
    Choice,
    DecisionModel,
    Noul,
    Score,
)
from openhands.sdk.llm.exceptions import LLMRateLimitError


MODEL = "typesafe/jev-latest"

QUESTIONS = {
    "urgent": Noul(instructions="Does this convey urgency?"),
    "team": Choice(
        instructions="Which team owns this?",
        criteria={"billing": "Charges and payments", "technical": "Broken features"},
    ),
    "impact": Score(
        instructions="How bad is the bug?",
        criteria=["Cosmetic", "Workaround available", "Work blocked"],
    ),
}

REPLY = {
    "model": "jev-1.13.0",
    "answers": {
        "urgent": {"type": "noul", "noul": 0.93},
        "team": {
            "type": "choice",
            "choice": "technical",
            "confidence": 0.8,
            "probabilities": {"billing": 0.1, "technical": 0.9},
        },
        "impact": {
            "type": "score",
            "score": 1.7,
            "confidence": 0.6,
            "legend": {
                "0": "Cosmetic",
                "1": "Workaround available",
                "2": "Work blocked",
            },
            "probabilities": {"0": 0.1, "1": 0.5, "2": 0.4},
        },
    },
    "usage": {"input_tokens": 1000, "output_tokens": 3},
}


@pytest.fixture
def provider() -> Iterator[tuple[str, deque, list]]:
    replies: deque = deque()
    requests: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(
                {"path": self.path, "auth": self.headers["Authorization"], "body": body}
            )
            status, reply = replies.popleft()
            payload = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", replies, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _model(base_url: str, **kwargs) -> DecisionModel:
    return DecisionModel(
        model=MODEL,
        api_key="test-key",
        base_url=base_url,
        retry_min_wait=0,
        retry_max_wait=0,
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
async def test_decide_sends_questions_and_returns_typed_answers(provider, use_async):
    base_url, replies, requests = provider
    replies.append((200, REPLY))
    model = _model(base_url)
    state = {"ticket": "PDF export fails"}

    if use_async:
        result = await model.adecide(state, QUESTIONS)
    else:
        result = model.decide(state, QUESTIONS)

    assert len(requests) == 1
    sent = requests[0]
    assert sent["auth"] == "Bearer test-key"
    assert sent["body"]["state"] == state
    assert sent["body"]["questions"] == {
        name: q.model_dump() for name, q in QUESTIONS.items()
    }

    assert result.noul("urgent").probability == 0.93
    assert result.choice("team").choice == "technical"
    assert result.choice("team").probabilities == {"billing": 0.1, "technical": 0.9}
    assert result.score("impact").score == 1.7

    usage = result.metrics.accumulated_token_usage
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens) == (1000, 3)
    assert result.metrics.accumulated_cost > 0
    assert model.metrics.accumulated_cost == result.metrics.accumulated_cost


def test_api_key_is_redacted_unless_secrets_are_exposed():
    model = DecisionModel(model=MODEL, api_key="sk-secret")

    assert "sk-secret" not in model.model_dump_json()
    assert "sk-secret" not in repr(model)
    assert model.model_dump(context={"expose_secrets": True})["api_key"] == "sk-secret"
    restored = DecisionModel.model_validate(
        model.model_dump(context={"expose_secrets": True})
    )
    assert isinstance(restored.api_key, SecretStr)
    assert restored.api_key.get_secret_value() == "sk-secret"


def test_answer_accessor_rejects_the_wrong_question_type(provider):
    base_url, replies, _ = provider
    replies.append((200, REPLY))

    result = _model(base_url).decide("x", QUESTIONS)

    with pytest.raises(TypeError, match="not a ChoiceAnswer"):
        result.choice("urgent")


def test_rate_limit_is_retried_then_succeeds(provider):
    base_url, replies, requests = provider
    replies.append((429, {"error": {"message": "slow down"}}))
    replies.append((200, REPLY))

    result = _model(base_url, num_retries=2).decide("x", QUESTIONS)

    assert len(requests) == 2
    assert result.noul("urgent").probability == 0.93


def test_rate_limit_exhaustion_raises_sdk_error(provider):
    base_url, replies, requests = provider
    for _ in range(2):
        replies.append((429, {"error": {"message": "slow down"}}))

    with pytest.raises(LLMRateLimitError):
        _model(base_url, num_retries=2).decide("x", QUESTIONS)

    assert len(requests) == 2


@pytest.fixture
def lmnr_exporter() -> Iterator[InMemorySpanExporter]:
    """Initialize lmnr for real, exporting to memory, then tear it down."""
    from lmnr import Laminar
    from lmnr.opentelemetry_lib.opentelemetry.instrumentation.threading import (
        ThreadingInstrumentor,
    )
    from lmnr.opentelemetry_lib.tracing import TracerWrapper
    from lmnr.opentelemetry_lib.tracing.processor import LaminarSpanProcessor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from openhands.sdk.observability import laminar

    assert not TracerWrapper.verify_initialized()
    exporter = InMemorySpanExporter()
    original_thread_init = threading.Thread.__init__
    Laminar.initialize(
        project_api_key="test-key",
        disable_batch=True,
        instruments=set(),
        set_global_tracer_provider=False,
    )
    processor = TracerWrapper.instance._span_processor
    assert isinstance(processor, LaminarSpanProcessor)
    processor.instance = SimpleSpanProcessor(exporter)
    laminar._observability_enabled = False
    try:
        yield exporter
    finally:
        laminar._observability_enabled = False
        Laminar.shutdown()
        ThreadingInstrumentor().uninstrument()
        threading.Thread.__init__ = original_thread_init  # type: ignore[method-assign]
        TracerWrapper._original_thread_init = None
        if hasattr(TracerWrapper, "instance"):
            del TracerWrapper.instance


def test_decision_call_emits_a_span_nested_under_the_active_span(
    provider, lmnr_exporter
):
    from lmnr import Laminar

    base_url, replies, _ = provider
    replies.append((200, REPLY))

    with Laminar.start_as_current_span(name="agent-step"):
        _model(base_url).decide("x", QUESTIONS)

    spans = {s.name: s for s in lmnr_exporter.get_finished_spans()}
    decision = spans[f"decision.{MODEL}"]
    parent = spans["agent-step"]
    assert decision.parent is not None
    assert decision.parent.span_id == parent.context.span_id
    attrs = decision.attributes
    assert attrs["gen_ai.request.model"] == MODEL
    assert json.loads(attrs["decision.answers"])["team"]["choice"] == "technical"
    assert attrs["gen_ai.usage.input_tokens"] == 1000
    assert attrs["gen_ai.usage.cost"] > 0
