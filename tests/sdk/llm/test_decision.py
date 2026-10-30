import datetime
import json
import threading
from collections import deque
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from litellm.types.decisions import DecisionsResponse
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import PrivateAttr, SecretStr, ValidationError

from openhands.sdk.llm import (
    Choice,
    ChoiceAnswer,
    DecisionModel,
    DecisionReply,
    DecisionUsage,
    Noul,
    NoulAnswer,
    NoulCriteria,
    Score,
    ScoreAnswer,
)
from openhands.sdk.llm.decision import _set_attributes
from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMRateLimitError,
    LLMResponseError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


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
    unexpected: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(
                {
                    "path": self.path,
                    "auth": self.headers["Authorization"],
                    "headers": dict(self.headers),
                    "body": body,
                }
            )
            if replies:
                status, reply = replies.popleft()
            else:
                unexpected.append(body)
                status, reply = 500, {"error": {"message": "unexpected extra request"}}
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
    assert unexpected == [], f"{len(unexpected)} request(s) beyond the scripted replies"


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
        "urgent": {"type": "noul", "instructions": "Does this convey urgency?"},
        "team": {
            "type": "choice",
            "instructions": "Which team owns this?",
            "criteria": {
                "billing": "Charges and payments",
                "technical": "Broken features",
            },
        },
        "impact": {
            "type": "score",
            "instructions": "How bad is the bug?",
            "criteria": ["Cosmetic", "Workaround available", "Work blocked"],
        },
    }

    assert result.noul("urgent").probability == 0.93
    assert result.choice("team").choice == "technical"
    assert result.choice("team").probabilities == {"billing": 0.1, "technical": 0.9}
    assert result.score("impact").score == 1.7
    assert result.score("impact").legend == {
        "0": "Cosmetic",
        "1": "Workaround available",
        "2": "Work blocked",
    }

    assert result.model == "jev-1.13.0"
    assert isinstance(result.raw_response, DecisionsResponse)

    usage = result.metrics.accumulated_token_usage
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens) == (1000, 3)
    assert result.metrics.accumulated_cost > 0
    assert model.metrics.accumulated_cost == result.metrics.accumulated_cost


def test_structured_instructions_and_criteria_are_sent_as_given(provider):
    base_url, replies, requests = provider
    levels = [{"what": "Harmless"}, {"what": "Clear phishing"}]
    questions = {
        "asks_credential": Noul(
            instructions={
                "question": "Does the message request a credential?",
                "inspect": "`ticket.message`",
            },
            criteria=NoulCriteria(
                true={"what": "Asks to disclose a password or key"},
                false="Does not ask for a credential",
            ),
        ),
        "team": Choice(
            instructions="Which team handles `ticket.message`?",
            criteria={
                "security": {"what": "Phishing", "examples": ["Send your password"]},
                "billing": {"what": "Charges", "not_for": "Phishing"},
            },
        ),
        "risk": Score(
            instructions=["How risky is", "`ticket.message`?"], criteria=levels
        ),
    }
    replies.append(
        (
            200,
            {
                "model": "jev-1.14.0",
                "answers": {
                    "asks_credential": {"type": "noul", "noul": 0.99},
                    "team": {
                        "type": "choice",
                        "choice": "security",
                        "confidence": 1.0,
                        "probabilities": {"security": 1.0, "billing": 0.0},
                    },
                    "risk": {
                        "type": "score",
                        "score": 1.0,
                        "confidence": 1.0,
                        "legend": {"0": levels[0], "1": levels[1]},
                        "probabilities": {"0": 0.0, "1": 1.0},
                    },
                },
            },
        )
    )

    result = _model(base_url).decide({"ticket": {"message": "hi"}}, questions)

    sent = requests[0]["body"]["questions"]
    assert sent["asks_credential"] == {
        "type": "noul",
        "instructions": {
            "question": "Does the message request a credential?",
            "inspect": "`ticket.message`",
        },
        "criteria": {
            "true": {"what": "Asks to disclose a password or key"},
            "false": "Does not ask for a credential",
        },
    }
    assert sent["team"]["criteria"]["billing"] == {
        "what": "Charges",
        "not_for": "Phishing",
    }
    assert sent["risk"] == {
        "type": "score",
        "instructions": ["How risky is", "`ticket.message`?"],
        "criteria": levels,
    }
    assert result.score("risk").legend == {"0": levels[0], "1": levels[1]}


@pytest.mark.parametrize(
    ("questions", "message"),
    [
        (
            {f"q{i}": Noul(instructions="Is it urgent?") for i in range(129)},
            "129 questions exceed LiteLLM's limit of 128",
        ),
        (
            {"team": Choice(criteria={f"team-{i}": None for i in range(256)})},
            "Choice 'team' has 256 options; LiteLLM accepts at most 255",
        ),
    ],
    ids=["questions", "choice-options"],
)
def test_requests_over_litellm_limits_fail_before_any_request(
    provider, questions, message
):
    base_url, _, requests = provider

    with pytest.raises(LLMBadRequestError, match=message):
        _model(base_url, num_retries=3).decide("state", questions)

    assert requests == []


def test_questions_accept_the_shapes_litellm_accepts(provider):
    base_url, replies, requests = provider
    replies.append(
        (
            200,
            {
                **REPLY,
                "answers": {
                    "urgent": REPLY["answers"]["urgent"],
                    "team": REPLY["answers"]["team"],
                },
            },
        )
    )
    questions = {
        "urgent": Noul(criteria=NoulCriteria(true="Work is blocked")),
        "team": Choice(criteria={"billing": None, "technical": "Broken features"}),
    }

    _model(base_url).decide("x", questions)

    assert requests[0]["body"]["questions"] == {
        "urgent": {"type": "noul", "criteria": {"true": "Work is blocked"}},
        "team": {
            "type": "choice",
            "criteria": {"billing": None, "technical": "Broken features"},
        },
    }


def test_noul_requires_instructions_or_criteria():
    with pytest.raises(ValidationError, match="requires instructions or criteria"):
        Noul()


def test_provider_options_reach_litellm(provider):
    base_url, replies, requests = provider
    replies.append((200, REPLY))
    model = _model(
        base_url,
        extra_headers={"X-Tenant": "acme"},
        aws_secret_access_key="aws-secret",
        aws_region_name="us-east-1",
    )

    model.decide("x", QUESTIONS)

    assert requests[0]["headers"]["X-Tenant"] == "acme"
    kwargs = model._litellm_kwargs("x", QUESTIONS)
    assert kwargs["aws_secret_access_key"] == "aws-secret"
    assert kwargs["aws_region_name"] == "us-east-1"
    assert "aws_access_key_id" not in kwargs


@pytest.mark.parametrize(
    "field",
    ["api_key", "aws_access_key_id", "aws_secret_access_key", "aws_session_token"],
)
def test_secrets_are_redacted_unless_exposed(field):
    model = DecisionModel.model_validate({"model": MODEL, field: "sk-secret"})

    assert "sk-secret" not in model.model_dump_json()
    assert "sk-secret" not in repr(model)
    exposed = model.model_dump(context={"expose_secrets": True})
    assert exposed[field] == "sk-secret"
    restored = DecisionModel.model_validate(exposed)
    secret = restored.model_dump()[field]
    assert isinstance(secret, SecretStr)
    assert secret.get_secret_value() == "sk-secret"


def test_answer_accessor_rejects_wrong_type_and_unknown_name(provider):
    base_url, replies, _ = provider
    replies.append((200, REPLY))

    result = _model(base_url).decide("x", QUESTIONS)

    with pytest.raises(TypeError, match="not a ChoiceAnswer"):
        result.choice("urgent")
    with pytest.raises(KeyError, match="'impact', 'team', 'urgent'"):
        result.noul("nope")


def test_missing_answer_raises_without_retry_and_keeps_usage(provider):
    base_url, replies, requests = provider
    answers = {k: v for k, v in REPLY["answers"].items() if k != "team"}
    replies.append((200, {**REPLY, "answers": answers}))
    model = _model(base_url, num_retries=3)

    with pytest.raises(LLMResponseError, match="no answer for: team"):
        model.decide("x", QUESTIONS)

    assert len(requests) == 1
    usage = model.metrics.accumulated_token_usage
    assert usage is not None and usage.prompt_tokens == 1000


def test_rate_limit_is_retried_then_succeeds(provider):
    base_url, replies, requests = provider
    replies.append((429, {"error": {"message": "slow down"}}))
    replies.append((200, REPLY))

    result = _model(base_url, num_retries=2).decide("x", QUESTIONS)

    assert len(requests) == 2
    assert result.noul("urgent").probability == 0.93


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
async def test_rate_limit_exhaustion_raises_sdk_error(provider, use_async):
    base_url, replies, requests = provider
    for _ in range(2):
        replies.append((429, {"error": {"message": "slow down"}}))
    model = _model(base_url, num_retries=2)

    with pytest.raises(LLMRateLimitError):
        if use_async:
            await model.adecide("x", QUESTIONS)
        else:
            model.decide("x", QUESTIONS)

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
    assert json.loads(attrs["lmnr.span.input"]) == {
        "questions": {
            name: q.model_dump(exclude_none=True) for name, q in QUESTIONS.items()
        }
    }
    assert json.loads(attrs["lmnr.span.output"])["team"]["choice"] == "technical"
    assert attrs["gen_ai.response.model"] == "jev-1.13.0"
    assert attrs["gen_ai.usage.input_tokens"] == 1000
    assert attrs["gen_ai.usage.cost"] > 0


def test_reply_without_usage_records_zero_tokens_and_no_cost(provider, lmnr_exporter):
    from lmnr import Laminar

    base_url, replies, _ = provider
    replies.append((200, {k: v for k, v in REPLY.items() if k != "usage"}))
    model = _model(base_url)

    with Laminar.start_as_current_span(name="agent-step"):
        result = model.decide("x", QUESTIONS)

    assert result.noul("urgent").probability == 0.93
    assert model.metrics.accumulated_cost == 0
    attrs = {s.name: s for s in lmnr_exporter.get_finished_spans()}[
        f"decision.{MODEL}"
    ].attributes
    assert attrs["gen_ai.usage.input_tokens"] == 0
    assert "gen_ai.usage.cost" not in attrs


class ScriptedDecisionModel(DecisionModel):
    """A provider LiteLLM lacks, added through the public send()/asend() hook.

    Each attempt pops the next scripted reply or exception, so the tests drive
    DecisionModel's real retry, metrics, span and error-mapping code.
    """

    _script: deque = PrivateAttr(default_factory=deque)
    _calls: list = PrivateAttr(default_factory=list)

    def send(self, state, questions):
        return self._next("send", state, questions)

    async def asend(self, state, questions):
        return self._next("asend", state, questions)

    def _next(self, method, state, questions):
        self._calls.append((method, state, dict(questions)))
        step = self._script.popleft()
        if isinstance(step, Exception):
            raise step
        return step


CUSTOM_REPLY = DecisionReply(
    answers={
        "urgent": NoulAnswer(probability=0.8),
        "team": ChoiceAnswer(
            choice="billing",
            confidence=0.6,
            probabilities={"billing": 0.8, "technical": 0.2},
        ),
        "impact": ScoreAnswer(
            score=0.5,
            confidence=0.4,
            probabilities={"0": 0.5, "1": 0.5, "2": 0.0},
            legend={"0": "Cosmetic", "1": "Workaround available", "2": "Work blocked"},
        ),
    },
    model="acme-decider-2.1",
    usage=DecisionUsage(input_tokens=50, output_tokens=2, cache_read_tokens=10),
    cost=0.25,
    raw_response={"id": "acme-123"},
)


def _scripted(*steps, **kwargs) -> ScriptedDecisionModel:
    model = ScriptedDecisionModel(
        model="acme-decider", retry_min_wait=0, retry_max_wait=0, **kwargs
    )
    model._script.extend(steps)
    return model


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
async def test_custom_send_returns_typed_result_with_metrics(use_async):
    model = _scripted(CUSTOM_REPLY)
    state = {"ticket": "PDF export fails"}

    if use_async:
        result = await model.adecide(state, QUESTIONS)
    else:
        result = model.decide(state, QUESTIONS)

    assert model._calls == [("asend" if use_async else "send", state, QUESTIONS)]
    assert result.answers == CUSTOM_REPLY.answers
    assert result.choice("team").choice == "billing"
    assert result.model == "acme-decider-2.1"
    assert result.raw_response == {"id": "acme-123"}
    usage = result.metrics.accumulated_token_usage
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens) == (50, 2)
    assert usage.cache_read_tokens == 10
    assert result.metrics.accumulated_cost == 0.25


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
async def test_custom_send_retries_transient_sdk_errors(use_async):
    model = _scripted(
        LLMRateLimitError("slow down"),
        LLMTimeoutError("timed out"),
        LLMServiceUnavailableError("down"),
        CUSTOM_REPLY,
        num_retries=4,
    )

    if use_async:
        result = await model.adecide("x", QUESTIONS)
    else:
        result = model.decide("x", QUESTIONS)

    assert len(model._calls) == 4
    assert result.noul("urgent").probability == 0.8


@pytest.mark.parametrize(
    "error",
    [LLMAuthenticationError("bad key"), ValueError("unknown option")],
    ids=["sdk-error", "other-error"],
)
def test_custom_send_errors_that_are_not_transient_raise_unchanged(error):
    model = _scripted(error, CUSTOM_REPLY, num_retries=3)

    with pytest.raises(type(error)) as raised:
        model.decide("x", QUESTIONS)

    assert raised.value is error
    assert len(model._calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
async def test_custom_send_error_keeps_its_cause(use_async):
    inner = ConnectionError("TLS handshake")
    error = LLMAuthenticationError("bad key")
    error.__cause__ = inner
    model = _scripted(error, num_retries=0)

    with pytest.raises(LLMAuthenticationError) as raised:
        if use_async:
            await model.adecide("x", QUESTIONS)
        else:
            model.decide("x", QUESTIONS)

    assert raised.value is error
    assert raised.value.__cause__ is inner


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        (
            {k: v for k, v in CUSTOM_REPLY.answers.items() if k != "impact"},
            "no answer for: impact",
        ),
        (
            {**CUSTOM_REPLY.answers, "urgent": CUSTOM_REPLY.answers["team"]},
            r"wrong answer type for: urgent \(ChoiceAnswer, expected NoulAnswer\)",
        ),
        (
            {**CUSTOM_REPLY.answers, "extra": NoulAnswer(probability=0.1)},
            "answers nobody asked for: extra",
        ),
    ],
    ids=["missing", "wrong-type", "unexpected"],
)
def test_custom_send_answers_that_do_not_match_raise_without_retry(answers, message):
    model = _scripted(
        CUSTOM_REPLY.model_copy(update={"answers": answers}), num_retries=3
    )

    with pytest.raises(LLMResponseError, match=message):
        model.decide("x", QUESTIONS)

    assert len(model._calls) == 1
    assert model.metrics.accumulated_cost == 0.25


def test_question_values_that_are_not_json_do_not_fail_the_call(lmnr_exporter):
    from lmnr import Laminar

    questions = {"urgent": Noul(instructions={"since": datetime.date(2026, 1, 1)})}
    reply = DecisionReply(answers={"urgent": NoulAnswer(probability=0.4)})

    assert _scripted(reply).decide("x", questions).noul("urgent").probability == 0.4

    with Laminar.start_as_current_span(name="agent-step"):
        _scripted(reply).decide("x", questions)

    attrs = {s.name: s for s in lmnr_exporter.get_finished_spans()}[
        "decision.acme-decider"
    ].attributes
    assert json.loads(attrs["lmnr.span.input"]) == {
        "questions": {
            "urgent": {"type": "noul", "instructions": {"since": "2026-01-01"}}
        }
    }


def test_a_rejected_span_attribute_skips_only_itself():
    class PickySpan:
        def __init__(self):
            self.attributes = {}

        def set_attribute(self, key, value):
            if key == "decision.answers":
                raise ValueError("too large")
            self.attributes[key] = value

    span = PickySpan()

    _set_attributes(
        span,
        {
            "decision.answers": "{}",
            "gen_ai.response.model": "m",
            "gen_ai.usage.cost": None,
        },
    )

    assert span.attributes == {"gen_ai.response.model": "m"}


def test_litellm_question_limit_does_not_apply_to_a_custom_send():
    questions = {f"q{i}": Noul(instructions="Is it urgent?") for i in range(200)}
    reply = DecisionReply(
        answers={name: NoulAnswer(probability=0.5) for name in questions}
    )

    result = _scripted(reply).decide("x", questions)

    assert len(result.answers) == 200


def test_custom_send_emits_the_decision_span(lmnr_exporter):
    from lmnr import Laminar

    with Laminar.start_as_current_span(name="agent-step"):
        _scripted(CUSTOM_REPLY).decide("x", QUESTIONS)

    spans = {s.name: s for s in lmnr_exporter.get_finished_spans()}
    decision = spans["decision.acme-decider"]
    assert decision.parent is not None
    assert decision.parent.span_id == spans["agent-step"].context.span_id
    attrs = decision.attributes
    assert attrs["gen_ai.request.model"] == "acme-decider"
    assert attrs["gen_ai.response.model"] == "acme-decider-2.1"
    assert attrs["gen_ai.usage.input_tokens"] == 50
    assert attrs["gen_ai.usage.output_tokens"] == 2
    assert attrs["gen_ai.usage.cost"] == 0.25
    assert json.loads(attrs["decision.answers"])["team"]["choice"] == "billing"


def test_custom_reply_without_model_usage_or_cost(lmnr_exporter):
    from lmnr import Laminar

    reply = DecisionReply(answers=CUSTOM_REPLY.answers)
    model = _scripted(reply)

    with Laminar.start_as_current_span(name="agent-step"):
        result = model.decide("x", QUESTIONS)

    assert result.model is None
    assert result.raw_response is None
    assert result.metrics.accumulated_cost == 0
    assert model.metrics.token_usages == []
    attrs = {s.name: s for s in lmnr_exporter.get_finished_spans()}[
        "decision.acme-decider"
    ].attributes
    assert "gen_ai.response.model" not in attrs
    assert "gen_ai.usage.input_tokens" not in attrs
    assert "gen_ai.usage.cost" not in attrs
