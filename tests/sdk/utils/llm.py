from __future__ import annotations

from unittest.mock import MagicMock

from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage


class TestLLM(LLM):
    """Deterministic LLM stub for tests.

    This avoids calling external providers while still exercising real
    agent/conversation code paths.
    """

    __test__ = False

    _response_text: str = PrivateAttr(default="ok")

    def __init__(self, *, model: str = "test-model", response_text: str = "ok"):
        super().__init__(model=model, usage_id="test-llm")
        self._response_text = response_text

    def completion(  # type: ignore[override]
        self, *, messages, tools=None, **kwargs
    ) -> LLMResponse:
        return self._make_response(response_id="test-completion")

    def responses(  # type: ignore[override]
        self, *, messages, tools=None, **kwargs
    ) -> LLMResponse:
        return self._make_response(response_id="test-responses")

    def _make_response(self, *, response_id: str) -> LLMResponse:
        message = Message(
            role="assistant",
            content=[TextContent(text=self._response_text)],
        )
        metrics = MetricsSnapshot(
            model_name=self.model,
            accumulated_cost=0.0,
            max_budget_per_task=0.0,
            accumulated_token_usage=TokenUsage(
                model=self.model,
                response_id=response_id,
            ),
        )
        return LLMResponse(
            message=message,
            metrics=metrics,
            raw_response=MagicMock(spec=ModelResponse, id=response_id),
        )
