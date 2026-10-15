"""Decision models: typed, classifier-style questions answered through LiteLLM.

A decision model takes a ``state`` and a map of named questions and returns one
typed answer per question (a probability, a choice, or a score) rather than
generated text. It sits next to :class:`LLM` and reuses its retry, exception
mapping, metrics and observability conventions, but it does not go through the
chat completion path.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Annotated, Any, ClassVar, Final, Literal, cast

from litellm import asystemone as litellm_asystemone, systemone as litellm_systemone
from litellm.types.decisions import DecisionsResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    field_serializer,
    field_validator,
)
from tenacity import retry_if_exception, retry_if_exception_type

from openhands.sdk.llm.exceptions import (
    is_quota_exhaustion_error,
    map_provider_exception,
)
from openhands.sdk.llm.exceptions.classifier import is_transient_http_error
from openhands.sdk.llm.llm import LLM_RETRY_EXCEPTIONS
from openhands.sdk.llm.utils.metrics import Metrics, MetricsSnapshot
from openhands.sdk.llm.utils.retry_mixin import RetryMixin
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.pydantic_secrets import serialize_secret, validate_secret


logger = get_logger(__name__)

__all__ = [
    "Choice",
    "ChoiceAnswer",
    "DecisionModel",
    "DecisionResult",
    "Noul",
    "NoulAnswer",
    "Question",
    "Score",
    "ScoreAnswer",
]

_MAX_SCORE_LEVELS: Final[int] = 10


class Noul(BaseModel):
    """A yes/no question. The answer is the probability of yes."""

    type: Literal["noul"] = "noul"
    instructions: str

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class Choice(BaseModel):
    """Pick one option from a closed set. ``criteria`` maps option to meaning."""

    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str] = Field(min_length=1)

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class Score(BaseModel):
    """Rate against ordered levels, lowest first."""

    type: Literal["score"] = "score"
    instructions: str
    criteria: list[str] = Field(min_length=1, max_length=_MAX_SCORE_LEVELS)

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


Question = Annotated[Noul | Choice | Score, Field(discriminator="type")]


class NoulAnswer(BaseModel):
    probability: float


class ChoiceAnswer(BaseModel):
    choice: str
    confidence: float
    probabilities: dict[str, float]


class ScoreAnswer(BaseModel):
    score: float
    confidence: float
    probabilities: dict[str, float]


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


class DecisionResult(BaseModel):
    """Answers keyed like the questions, plus the call's metrics."""

    answers: dict[str, Answer]
    metrics: MetricsSnapshot
    raw_response: DecisionsResponse

    model_config: ClassVar[ConfigDict] = ConfigDict(arbitrary_types_allowed=True)

    def noul(self, name: str) -> NoulAnswer:
        return self._answer(name, NoulAnswer)

    def choice(self, name: str) -> ChoiceAnswer:
        return self._answer(name, ChoiceAnswer)

    def score(self, name: str) -> ScoreAnswer:
        return self._answer(name, ScoreAnswer)

    def _answer[A: BaseModel](self, name: str, kind: type[A]) -> A:
        answer = self.answers[name]
        if not isinstance(answer, kind):
            raise TypeError(
                f"Question {name!r} has a {type(answer).__name__}, "
                f"not a {kind.__name__}"
            )
        return answer


def _convert_answer(answer: Any) -> Answer:
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
            )
    raise ValueError(f"Unsupported decision answer type: {answer.type!r}")


class DecisionModel(BaseModel, RetryMixin):
    """Ask typed questions of a decision model through LiteLLM.

    ``model`` is a LiteLLM model string for a decision provider. Like
    :class:`LLM`, the SDK holds no per-provider knowledge: routing, wire format
    and pricing all belong to LiteLLM.
    """

    model: str = Field(description="LiteLLM model string of a decision model.")
    api_key: str | SecretStr | None = Field(default=None, description="API key.")
    base_url: str | None = Field(default=None, description="Custom API base URL.")
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

    @field_validator("api_key")
    @classmethod
    def _validate_secrets(cls, v: str | SecretStr | None, info) -> SecretStr | None:
        return validate_secret(v, info)

    @field_serializer("api_key", when_used="always")
    def _serialize_secrets(self, v: SecretStr | None, info):
        return serialize_secret(v, info)

    def model_post_init(self, __context: Any) -> None:
        self._metrics = Metrics(model_name=self.model)

    @property
    def metrics(self) -> Metrics:
        return self._metrics

    def decide(self, state: Any, questions: Mapping[str, Question]) -> DecisionResult:
        kwargs = self._call_kwargs(state, questions)

        @self._retry_decorator()
        def _one_attempt() -> DecisionResult:
            with self._observed_call(questions) as annotate:
                started = time.time()
                response = cast(DecisionsResponse, litellm_systemone(**kwargs))
                return annotate(response, time.time() - started)

        try:
            return _one_attempt()
        except Exception as e:
            mapped = map_provider_exception(e)
            if mapped is not e:
                raise mapped from e
            raise

    async def adecide(
        self, state: Any, questions: Mapping[str, Question]
    ) -> DecisionResult:
        kwargs = self._call_kwargs(state, questions)

        @self._retry_decorator()
        async def _one_attempt() -> DecisionResult:
            with self._observed_call(questions) as annotate:
                started = time.time()
                response = cast(DecisionsResponse, await litellm_asystemone(**kwargs))
                return annotate(response, time.time() - started)

        try:
            return await _one_attempt()
        except Exception as e:
            mapped = map_provider_exception(e)
            if mapped is not e:
                raise mapped from e
            raise

    def _call_kwargs(
        self, state: Any, questions: Mapping[str, Question]
    ) -> dict[str, Any]:
        api_key = self.api_key
        assert api_key is None or isinstance(api_key, SecretStr)
        return {
            "model": self.model,
            "state": state,
            "questions": {name: q.model_dump() for name, q in questions.items()},
            "api_key": api_key.get_secret_value() if api_key else None,
            "api_base": self.base_url,
            "timeout": self.timeout,
            "drop_params": self.drop_params,
            # The SDK owns retries, as in LLM.
            "max_retries": 0,
        }

    def _retry_decorator(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        retryable = (
            retry_if_exception_type(LLM_RETRY_EXCEPTIONS)
            | retry_if_exception(is_transient_http_error)
        ) & retry_if_exception(lambda e: not is_quota_exhaustion_error(e))
        return self.retry_decorator(
            num_retries=self.num_retries,
            retry_exceptions=retryable,
            retry_min_wait=self.retry_min_wait,
            retry_max_wait=self.retry_max_wait,
            retry_multiplier=self.retry_multiplier,
        )

    @contextmanager
    def _observed_call(
        self, questions: Mapping[str, Question]
    ) -> Iterator[Callable[[DecisionsResponse, float], DecisionResult]]:
        """One attempt: a span that is current while LiteLLM runs.

        Yields a function that records the response on the metrics and the span
        and returns the SDK result. The state is deliberately not recorded on
        the span, because it may carry sensitive content.
        """
        # Lazy: observability imports openhands.sdk.event, which imports llm.
        from openhands.sdk.observability.laminar import llm_call_span

        with llm_call_span(f"decision.{self.model}") as span:
            _set_attributes(
                span,
                {
                    "gen_ai.request.model": self.model,
                    "decision.questions": json.dumps(
                        {name: q.model_dump() for name, q in questions.items()}
                    ),
                },
            )

            def annotate(response: DecisionsResponse, latency: float) -> DecisionResult:
                return self._finish(response, latency, span)

            yield annotate

    def _finish(
        self, response: DecisionsResponse, latency: float, span: Any
    ) -> DecisionResult:
        response_id = uuid.uuid4().hex
        self._metrics.add_response_latency(latency, response_id)
        cost = response.hidden_params.get("response_cost")
        if isinstance(cost, int | float) and cost > 0:
            self._metrics.add_cost(float(cost))
        usage = response.usage
        if usage is not None:
            self._metrics.add_token_usage(
                prompt_tokens=usage.input_tokens,
                completion_tokens=usage.output_tokens,
                cache_read_tokens=usage.cached_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                context_window=0,
                response_id=response_id,
            )

        answers = {name: _convert_answer(a) for name, a in response.answers.items()}
        attributes: dict[str, Any] = {
            "decision.answers": json.dumps(
                {name: a.model_dump() for name, a in answers.items()}
            )
        }
        if usage is not None:
            attributes["gen_ai.usage.input_tokens"] = usage.input_tokens
            attributes["gen_ai.usage.output_tokens"] = usage.output_tokens
        if isinstance(cost, int | float) and cost > 0:
            attributes["gen_ai.usage.cost"] = float(cost)
        _set_attributes(span, attributes)

        return DecisionResult(
            answers=answers,
            metrics=self._metrics.get_snapshot(),
            raw_response=response,
        )


def _set_attributes(span: Any, attributes: Mapping[str, Any]) -> None:
    if span is None:
        return
    try:
        for key, value in attributes.items():
            span.set_attribute(key, value)
    except Exception:
        logger.debug("Failed to annotate decision span", exc_info=True)
