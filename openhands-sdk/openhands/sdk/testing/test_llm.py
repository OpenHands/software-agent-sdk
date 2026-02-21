"""TestLLM - A mock LLM for testing.

TestLLM is a real LLM subclass that returns scripted responses, eliminating
the need for @patch decorators and understanding of LiteLLM internals.

Example:
    >>> from openhands.sdk.testing import TestLLM
    >>> from openhands.sdk.llm import Message, TextContent
    >>>
    >>> # Create a TestLLM with scripted responses
    >>> llm = TestLLM.from_messages([
    ...     Message(role="assistant", content=[TextContent(text="Hello!")]),
    ...     Message(role="assistant", content=[TextContent(text="Goodbye!")]),
    ... ])
    >>>
    >>> # Use it like a normal LLM
    >>> user_msg = Message(role="user", content=[TextContent(text="Hi")])
    >>> response = llm.completion([user_msg])
    >>> print(response.message.content[0].text)  # "Hello!"

    >>> # Scripted errors (like unittest.mock side_effect)
    >>> from openhands.sdk.llm.exceptions import LLMContextWindowExceedError
    >>> llm = TestLLM.from_responses([
    ...     Message(role="assistant", content=[TextContent(text="OK")]),
    ...     LLMContextWindowExceedError(),
    ... ])
    >>> llm.completion([...])  # returns "OK"
    >>> llm.completion([...])  # raises LLMContextWindowExceedError
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse
from pydantic import ConfigDict, Field, PrivateAttr

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.message import Message
from openhands.sdk.llm.streaming import TokenCallbackType
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage


if TYPE_CHECKING:
    from openhands.sdk.tool.tool import ToolDefinition

from collections import deque


__all__ = ["TestLLM", "TestLLMExhaustedError"]


class TestLLMExhaustedError(Exception):
    """Raised when TestLLM has no more scripted responses."""

    pass


class TestLLM(LLM):
    """A mock LLM for testing that returns scripted responses.

    TestLLM is a real LLM subclass that can be used anywhere an LLM is accepted:
    in Agent(llm=...), in fallback_llms, in condensers, in routers, etc.

    Key features:
    - No patching needed: just pass TestLLM as the llm= argument
    - Tests speak in SDK types (Message, TextContent, MessageToolCall)
    - Clear error when responses are exhausted
    - Zero-cost metrics by default
    - Always uses completion() path (uses_responses_api returns False)

    Example:
        >>> from openhands.sdk.testing import TestLLM
        >>> from openhands.sdk.llm import Message, TextContent, MessageToolCall
        >>>
        >>> # Simple text response
        >>> llm = TestLLM.from_messages([
        ...     Message(role="assistant", content=[TextContent(text="Done!")]),
        ... ])
        >>>
        >>> # Response with tool calls
        >>> llm = TestLLM.from_messages([
        ...     Message(
        ...         role="assistant",
        ...         content=[TextContent(text="")],
        ...         tool_calls=[
        ...             MessageToolCall(
        ...                 id="call_1",
        ...                 name="my_tool",
        ...                 arguments='{"arg": "value"}',
        ...                 origin="completion",
        ...             )
        ...         ],
        ...     ),
        ...     Message(role="assistant", content=[TextContent(text="Done!")]),
        ... ])
    """

    # Prevent pytest from collecting this class as a test
    __test__ = False

    model: str = Field(default="test-model")
    _scripted_responses: deque[Message | Exception] = PrivateAttr(default_factory=deque)
    _call_count: int = PrivateAttr(default=0)

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", arbitrary_types_allowed=True
    )

    def __init__(self, **data: Any) -> None:
        # Extract scripted_responses before calling super().__init__
        scripted_responses = data.pop("scripted_responses", [])
        super().__init__(**data)
        self._scripted_responses = deque(list(scripted_responses))
        self._call_count = 0

    @classmethod
    def from_messages(
        cls,
        messages: list[Message | Exception],
        *,
        model: str = "test-model",
        usage_id: str = "test-llm",
        **kwargs: Any,
    ) -> TestLLM:
        """Create a TestLLM with scripted responses and/or errors.

        Args:
            messages: List of Message or Exception objects to return in order.
                Each call to completion() or responses() consumes the next
                item: Message objects are returned normally, Exception objects
                are raised (like unittest.mock side_effect).
            model: Model name (default: "test-model")
            usage_id: Usage ID for metrics (default: "test-llm")
            **kwargs: Additional LLM configuration options

        Returns:
            A TestLLM instance configured with the scripted responses.

        Example:
            >>> llm = TestLLM.from_messages([
            ...     Message(role="assistant", content=[TextContent(text="First")]),
            ...     LLMContextWindowExceedError("context too long"),
            ... ])
        """
        return cls(
            model=model,
            usage_id=usage_id,
            scripted_responses=messages,
            **kwargs,
        )

    def completion(
        self,
        messages: list[Message],  # noqa: ARG002
        tools: Sequence[ToolDefinition] | None = None,  # noqa: ARG002
        _return_metrics: bool = False,
        add_security_risk_prediction: bool = False,  # noqa: ARG002
        on_token: TokenCallbackType | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> LLMResponse:
        """Return the next scripted response.

        Args:
            messages: Input messages (ignored, but required for API compatibility)
            tools: Available tools (ignored)
            _return_metrics: Whether to return metrics (ignored)
            add_security_risk_prediction: Add security risk field (ignored)
            on_token: Streaming callback (ignored)
            **kwargs: Additional arguments (ignored)

        Returns:
            LLMResponse containing the next scripted message.

        Raises:
            TestLLMExhaustedError: When no more scripted responses are available.
            Exception: Any scripted exception placed in the response queue.
        """
        if not self._scripted_responses:
            raise TestLLMExhaustedError(
                f"TestLLM: no more scripted responses "
                f"(exhausted after {self._call_count} calls)"
            )

        item = self._scripted_responses.popleft()
        self._call_count += 1

        # Raise scripted exceptions (like unittest.mock side_effect)
        if isinstance(item, Exception):
            raise item

        message = item

        # Create a minimal ModelResponse for raw_response
        raw_response = self._create_model_response(message)

        return LLMResponse(
            message=message,
            metrics=self._zero_metrics(),
            raw_response=raw_response,
        )

    def responses(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        include: list[str] | None = None,  # noqa: ARG002
        store: bool | None = None,  # noqa: ARG002
        _return_metrics: bool = False,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Return the next scripted response (delegates to completion).

        For TestLLM, both completion() and responses() return from the same
        queue of scripted responses.
        """
        return self.completion(
            messages=messages,
            tools=tools,
            _return_metrics=_return_metrics,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            **kwargs,
        )

    def uses_responses_api(self) -> bool:
        """TestLLM always uses the completion path."""
        return False

    def _zero_metrics(self) -> MetricsSnapshot:
        """Return a zero-cost metrics snapshot."""
        return MetricsSnapshot(
            model_name=self.model,
            accumulated_cost=0.0,
            max_budget_per_task=None,
            accumulated_token_usage=TokenUsage(
                model=self.model,
                prompt_tokens=0,
                completion_tokens=0,
            ),
        )

    def _create_model_response(self, message: Message) -> ModelResponse:
        """Create a minimal ModelResponse from a Message.

        This creates a valid ModelResponse that can be used as raw_response
        in LLMResponse.
        """
        # Build the LiteLLM message dict
        litellm_message_dict: dict[str, Any] = {
            "role": message.role,
            "content": self._content_to_string(message),
        }

        # Add tool_calls if present
        if message.tool_calls:
            litellm_message_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        litellm_message = LiteLLMMessage(**litellm_message_dict)

        return ModelResponse(
            id=f"test-response-{self._call_count}",
            choices=[Choices(message=litellm_message, index=0, finish_reason="stop")],
            created=0,
            model=self.model,
            object="chat.completion",
        )

    def _content_to_string(self, message: Message) -> str:
        """Convert message content to a string."""
        from openhands.sdk.llm.message import TextContent

        parts = []
        for item in message.content:
            if isinstance(item, TextContent):
                parts.append(item.text)
        return "\n".join(parts)

    @property
    def remaining_responses(self) -> int:
        """Return the number of remaining scripted responses."""
        return len(self._scripted_responses)

    @property
    def call_count(self) -> int:
        """Return the number of calls made to this TestLLM."""
        return self._call_count
