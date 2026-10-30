"""Decision models: typed, classifier-style questions and answers.

A decision model takes a ``state`` and a map of named questions and returns one
typed answer per question (a probability, a choice, or a score) rather than
generated text. It sits next to :class:`LLM` and reuses its retry, exception
mapping, metrics and observability conventions, but it does not go through the
chat completion path.

Questions and answers are SDK types. LiteLLM is the default provider, and it
is used only inside :meth:`DecisionModel.send` and :meth:`DecisionModel.asend`.
A subclass can override those two methods to call a provider LiteLLM lacks.
"""

import json
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Annotated, Any, ClassVar, Final, Literal

from litellm import asystemone as litellm_asystemone, systemone as litellm_systemone
from litellm.types.decisions import MAX_DECISION_QUESTIONS, DecisionsResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

from openhands.sdk.llm.exceptions import (
    LLMBadRequestError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
    map_provider_exception,
)
from openhands.sdk.llm.llm import LLM_RETRY_EXCEPTIONS
from openhands.sdk.llm.utils.metrics import Metrics, MetricsSnapshot
from openhands.sdk.llm.utils.retry_mixin import RetryMixin, transient_error_condition
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.pydantic_secrets import serialize_secret, validate_secret


logger = get_logger(__name__)

__all__ = [
    "Choice",
    "ChoiceAnswer",
    "DecisionJSON",
    "DecisionModel",
    "DecisionReply",
    "DecisionResult",
    "DecisionUsage",
    "Noul",
    "NoulAnswer",
    "NoulCriteria",
    "Question",
    "Score",
    "ScoreAnswer",
]

_MAX_SCORE_LEVELS: Final[int] = 10
_SECRET_FIELDS: Final[tuple[str, ...]] = (
    "api_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
)
# LiteLLM's ChoiceQuestion caps criteria at 255 but exports no constant for it.
_LITELLM_MAX_CHOICE_OPTIONS: Final[int] = 255

# SDK errors that a custom send() raises for failures worth retrying, on top
# of the LiteLLM errors LLM already retries.
_RETRY_EXCEPTIONS: Final[tuple[type[Exception], ...]] = (
    *LLM_RETRY_EXCEPTIONS,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMServiceUnavailableError,
)

# Instructions and criteria may be plain text or JSON structure, which keeps
# the question apart from the context and examples that guide it.
type DecisionJSON = str | dict[str, Any] | list[Any]


class NoulCriteria(BaseModel):
    """What makes a Noul's answer true, and what makes it false."""

    true: DecisionJSON | None = None
    false: DecisionJSON | None = None


class _QuestionBase(BaseModel):
    instructions: DecisionJSON | None = None


class Noul(_QuestionBase):
    """A yes/no question. The answer is the probability of yes."""

    type: Literal["noul"] = "noul"
    criteria: NoulCriteria | None = None

    @model_validator(mode="after")
    def _require_instructions_or_criteria(self) -> "Noul":
        if self.instructions is None and self.criteria is None:
            raise ValueError("A Noul requires instructions or criteria")
        return self


class Choice(_QuestionBase):
    """Pick one option from a closed set.

    ``criteria`` maps each option to an optional description of it.
    """

    type: Literal["choice"] = "choice"
    criteria: Mapping[str, DecisionJSON | None] = Field(min_length=1)


class Score(_QuestionBase):
    """Rate against ordered levels, lowest first."""

    type: Literal["score"] = "score"
    criteria: Sequence[DecisionJSON] = Field(min_length=1, max_length=_MAX_SCORE_LEVELS)


Question = Annotated[Noul | Choice | Score, Field(discriminator="type")]


class NoulAnswer(BaseModel):
    probability: float


class ChoiceAnswer(BaseModel):
    choice: str
    confidence: float
    probabilities: dict[str, float]


class ScoreAnswer(BaseModel):
    """``probabilities`` and ``legend`` are keyed by level index, as a string."""

    score: float
    confidence: float
    probabilities: dict[str, float]
    legend: dict[str, DecisionJSON]


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer

_ANSWER_TYPES: Final[dict[str, type[Answer]]] = {
    "noul": NoulAnswer,
    "choice": ChoiceAnswer,
    "score": ScoreAnswer,
}


class DecisionUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class DecisionReply(BaseModel):
    """One provider reply in SDK types, as returned by ``DecisionModel.send``.

    ``model`` is the versioned model that answered. ``usage`` and ``cost`` are
    ``None`` when the provider does not report them. ``raw_response`` keeps the
    provider's own payload for debugging.
    """

    answers: dict[str, Answer]
    model: str | None = None
    usage: DecisionUsage | None = None
    cost: float | None = None
    raw_response: Any = None


class DecisionResult(BaseModel):
    """Answers keyed like the questions.

    ``metrics`` is a snapshot of the model's running totals, as in
    ``LLMResponse``, not the cost of this call alone.
    """

    answers: dict[str, Answer]
    model: str | None = Field(description="The versioned model that answered.")
    metrics: MetricsSnapshot
    raw_response: Any = Field(default=None, description="The provider's payload.")

    def noul(self, name: str) -> NoulAnswer:
        return self._answer(name, NoulAnswer)

    def choice(self, name: str) -> ChoiceAnswer:
        return self._answer(name, ChoiceAnswer)

    def score(self, name: str) -> ScoreAnswer:
        return self._answer(name, ScoreAnswer)

    def _answer[A: BaseModel](self, name: str, kind: type[A]) -> A:
        answer = self.answers.get(name)
        if answer is None:
            raise KeyError(f"No answer named {name!r}; answers: {sorted(self.answers)}")
        if not isinstance(answer, kind):
            raise TypeError(
                f"Question {name!r} has a {type(answer).__name__}, "
                f"not a {kind.__name__}"
            )
        return answer


class DecisionModel(BaseModel, RetryMixin):
    """Ask typed questions of a decision model.

    By default ``model`` is a LiteLLM model string and LiteLLM owns routing,
    wire format and pricing, as it does for :class:`LLM`. To use a provider
    LiteLLM lacks, subclass and override :meth:`send` (and :meth:`asend` for
    :meth:`adecide`). Retries, error mapping, metrics and the
    ``decision.<model>`` span still apply.
    """

    model: str = Field(
        description="Decision model name; a LiteLLM model string by default."
    )
    api_key: str | SecretStr | None = Field(default=None, description="API key.")
    base_url: str | None = Field(default=None, description="Custom API base URL.")
    custom_llm_provider: str | None = Field(
        default=None, description="LiteLLM provider, when the model string lacks it."
    )
    extra_headers: dict[str, str] | None = Field(
        default=None, description="HTTP headers to forward to the provider."
    )
    safety_identifier: str | None = Field(
        default=None, description="Stable end-user identifier for abuse monitoring."
    )
    aws_access_key_id: str | SecretStr | None = None
    aws_secret_access_key: str | SecretStr | None = None
    aws_session_token: str | SecretStr | None = None
    aws_region_name: str | None = None
    aws_profile_name: str | None = None
    aws_role_name: str | None = None
    aws_session_name: str | None = None
    timeout: int | None = Field(
        default=300, ge=0, description="HTTP timeout per attempt, in seconds."
    )
    num_retries: int = Field(default=5, ge=0)
    retry_multiplier: float = Field(default=8.0, ge=0)
    retry_min_wait: int = Field(default=8, ge=0)
    retry_max_wait: int = Field(default=64, ge=0)
    drop_params: bool = Field(default=True)

    _metrics: Metrics = PrivateAttr()

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    @field_validator(*_SECRET_FIELDS)
    @classmethod
    def _validate_secrets(cls, v: str | SecretStr | None, info) -> SecretStr | None:
        return validate_secret(v, info)

    @field_serializer(*_SECRET_FIELDS, when_used="always")
    def _serialize_secrets(self, v: SecretStr | None, info):
        return serialize_secret(v, info)

    def model_post_init(self, __context: Any) -> None:
        self._metrics = Metrics(model_name=self.model)

    @property
    def metrics(self) -> Metrics:
        return self._metrics

    def decide(self, state: Any, questions: Mapping[str, Question]) -> DecisionResult:
        """Answer every question about ``state`` in one call.

        Questions run in parallel against the same state, so ask all of them
        at once. Through LiteLLM a call takes at most ``MAX_DECISION_QUESTIONS``
        (128) questions; split larger sets across calls with the same state.
        """

        @self._retry_decorator()
        def _one_attempt() -> DecisionResult:
            with self._observed_call(questions) as finish:
                started = time.time()
                reply = self.send(state, questions)
                return finish(reply, time.time() - started)

        try:
            return _one_attempt()
        except Exception as e:
            mapped = _sdk_error(e)
            if mapped is e:
                raise
            raise mapped from e

    async def adecide(
        self, state: Any, questions: Mapping[str, Question]
    ) -> DecisionResult:
        """Async :meth:`decide`."""

        @self._retry_decorator()
        async def _one_attempt() -> DecisionResult:
            with self._observed_call(questions) as finish:
                started = time.time()
                reply = await self.asend(state, questions)
                return finish(reply, time.time() - started)

        try:
            return await _one_attempt()
        except Exception as e:
            mapped = _sdk_error(e)
            if mapped is e:
                raise
            raise mapped from e

    def send(self, state: Any, questions: Mapping[str, Question]) -> DecisionReply:
        """Make one provider request; :meth:`decide` calls this once per attempt.

        The default sends through LiteLLM. Override it to call another
        provider. Raise ``LLMRateLimitError``, ``LLMTimeoutError`` or
        ``LLMServiceUnavailableError`` for failures worth retrying.
        """
        response = litellm_systemone(**self._litellm_kwargs(state, questions))
        return _reply_from_litellm(response)

    async def asend(
        self, state: Any, questions: Mapping[str, Question]
    ) -> DecisionReply:
        """Async :meth:`send`, used by :meth:`adecide`."""
        response = await litellm_asystemone(**self._litellm_kwargs(state, questions))
        return _reply_from_litellm(response)

    def _litellm_kwargs(
        self, state: Any, questions: Mapping[str, Question]
    ) -> dict[str, Any]:
        _check_litellm_limits(questions)
        return {
            "model": self.model,
            "state": state,
            "questions": _dump_questions(questions),
            "api_key": _secret_value(self.api_key),
            "api_base": self.base_url,
            "timeout": self.timeout,
            "custom_llm_provider": self.custom_llm_provider,
            "extra_headers": self.extra_headers,
            "safety_identifier": self.safety_identifier,
            "drop_params": self.drop_params,
            **self._aws_kwargs(),
        }

    def _aws_kwargs(self) -> dict[str, str]:
        kwargs = {
            "aws_access_key_id": _secret_value(self.aws_access_key_id),
            "aws_secret_access_key": _secret_value(self.aws_secret_access_key),
            "aws_session_token": _secret_value(self.aws_session_token),
            "aws_region_name": self.aws_region_name,
            "aws_profile_name": self.aws_profile_name,
            "aws_role_name": self.aws_role_name,
            "aws_session_name": self.aws_session_name,
        }
        return {key: value for key, value in kwargs.items() if value}

    def _retry_decorator(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self.retry_decorator(
            num_retries=self.num_retries,
            retry_exceptions=transient_error_condition(_RETRY_EXCEPTIONS),
            retry_min_wait=self.retry_min_wait,
            retry_max_wait=self.retry_max_wait,
            retry_multiplier=self.retry_multiplier,
        )

    @contextmanager
    def _observed_call(
        self, questions: Mapping[str, Question]
    ) -> Iterator[Callable[[DecisionReply, float], DecisionResult]]:
        """One attempt: a span that is current while the provider runs.

        Yields a function that records the reply on the metrics and the span
        and returns the result. The state is deliberately not recorded on the
        span, because it may carry sensitive content.
        """
        # Lazy: observability imports openhands.sdk.event, which imports llm.
        from openhands.sdk.observability.laminar import llm_call_span

        with llm_call_span(f"decision.{self.model}") as span:
            if span is not None:
                questions_json = _to_json(_dump_questions(questions))
                # Laminar renders lmnr.span.input/output as the span's Input
                # and Output panes. The state stays out, as above.
                span_input = (
                    None
                    if questions_json is None
                    else f'{{"questions": {questions_json}}}'
                )
                _set_attributes(
                    span,
                    {
                        "gen_ai.request.model": self.model,
                        "decision.questions": questions_json,
                        "lmnr.span.input": span_input,
                    },
                )

            def finish(reply: DecisionReply, latency: float) -> DecisionResult:
                return self._finish(reply, latency, span, questions)

            yield finish

    def _finish(
        self,
        reply: DecisionReply,
        latency: float,
        span: Any,
        questions: Mapping[str, Question],
    ) -> DecisionResult:
        response_id = uuid.uuid4().hex
        self._metrics.add_response_latency(latency, response_id)
        cost = reply.cost if reply.cost is not None and reply.cost > 0 else None
        if cost is not None:
            self._metrics.add_cost(cost)
        usage = reply.usage
        if usage is not None:
            self._metrics.add_token_usage(
                prompt_tokens=usage.input_tokens,
                completion_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                context_window=0,
                response_id=response_id,
            )

        if span is not None:
            answers_json = _to_json(
                {name: a.model_dump() for name, a in reply.answers.items()}
            )
            _set_attributes(
                span,
                {
                    "decision.answers": answers_json,
                    "lmnr.span.output": answers_json,
                    "gen_ai.response.model": reply.model,
                    "gen_ai.usage.input_tokens": usage and usage.input_tokens,
                    "gen_ai.usage.output_tokens": usage and usage.output_tokens,
                    "gen_ai.usage.cost": cost,
                },
            )

        _check_answers(questions, reply.answers)

        return DecisionResult(
            answers=reply.answers,
            model=reply.model,
            metrics=self._metrics.get_snapshot(),
            raw_response=reply.raw_response,
        )


def _secret_value(secret: str | SecretStr | None) -> str | None:
    assert secret is None or isinstance(secret, SecretStr)
    return secret.get_secret_value() if secret else None


def _check_litellm_limits(questions: Mapping[str, Question]) -> None:
    if len(questions) > MAX_DECISION_QUESTIONS:
        raise LLMBadRequestError(
            f"{len(questions)} questions exceed LiteLLM's limit of "
            f"{MAX_DECISION_QUESTIONS} per call; split them across calls "
            "with the same state"
        )
    for name, question in questions.items():
        if (
            isinstance(question, Choice)
            and len(question.criteria) > _LITELLM_MAX_CHOICE_OPTIONS
        ):
            raise LLMBadRequestError(
                f"Choice {name!r} has {len(question.criteria)} options; LiteLLM "
                f"accepts at most {_LITELLM_MAX_CHOICE_OPTIONS}"
            )


def _check_answers(
    questions: Mapping[str, Question], answers: Mapping[str, Answer]
) -> None:
    missing = [name for name in questions if name not in answers]
    wrong_type = [
        f"{name} ({type(answers[name]).__name__}, "
        f"expected {_ANSWER_TYPES[question.type].__name__})"
        for name, question in questions.items()
        if name in answers
        and not isinstance(answers[name], _ANSWER_TYPES[question.type])
    ]
    unexpected = [name for name in answers if name not in questions]
    problems = [
        f"{label}: {', '.join(names)}"
        for label, names in (
            ("no answer for", missing),
            ("wrong answer type for", wrong_type),
            ("answers nobody asked for", unexpected),
        )
        if names
    ]
    if problems:
        raise LLMResponseError(f"Decision model returned {'; '.join(problems)}")


def _sdk_error(e: Exception) -> Exception:
    """SDK errors, including those a custom send() raises, pass through."""
    if isinstance(e, LLMError):
        return e
    return map_provider_exception(e)


def _reply_from_litellm(response: DecisionsResponse) -> DecisionReply:
    cost = response.hidden_params.get("response_cost")
    usage = response.usage
    return DecisionReply(
        answers={name: _answer_from_litellm(a) for name, a in response.answers.items()},
        model=response.model,
        usage=None
        if usage is None
        else DecisionUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cached_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        ),
        cost=float(cost) if isinstance(cost, int | float) else None,
        raw_response=response,
    )


def _answer_from_litellm(answer: Any) -> Answer:
    match answer.type:
        case "noul":
            return NoulAnswer(probability=answer.noul)
        case "choice":
            return ChoiceAnswer(
                choice=answer.choice,
                confidence=answer.confidence,
                probabilities=dict(answer.probabilities),
            )
        case "score":
            return ScoreAnswer(
                score=answer.score,
                confidence=answer.confidence,
                probabilities=dict(answer.probabilities),
                legend=dict(answer.legend),
            )
    raise ValueError(f"Unsupported decision answer type: {answer.type!r}")


def _dump_questions(questions: Mapping[str, Question]) -> dict[str, Any]:
    # exclude_none leaves out unset optional fields such as Noul.criteria.
    return {name: q.model_dump(exclude_none=True) for name, q in questions.items()}


def _to_json(value: Any) -> str | None:
    """JSON for a span attribute, or None; tracing must never fail the call."""
    try:
        return json.dumps(value, default=str)
    except Exception:
        logger.debug("Failed to serialize decision span attribute", exc_info=True)
        return None


def _set_attributes(span: Any, attributes: Mapping[str, Any]) -> None:
    """Set each attribute that has a value; one rejected key skips only itself."""
    for key, value in attributes.items():
        if value is None:
            continue
        try:
            span.set_attribute(key, value)
        except Exception:
            logger.debug("Failed to set decision span attribute %s", key, exc_info=True)
