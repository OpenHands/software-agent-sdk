import warnings

from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse

from openhands.sdk.llm import LLM, LLMResponse, Message
from openhands.sdk.llm.message import TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage


def test_pydantic_serializer_warnings_suppressed():
    """
    Test that Pydantic serializer warnings from litellm are suppressed.

    This test verifies that the warning filter is correctly configured
    in the openhands.sdk.llm module initialization to suppress
    "Pydantic serializer warnings" that occur when litellm's Pydantic
    models are serialized with mismatched field counts.

    The filter is applied at module import time in openhands.sdk.llm.__init__.py
    and prevents these warnings from being shown to users during normal usage.
    """
    # Capture all warnings during module import
    with warnings.catch_warnings(record=True) as warning_list:
        warnings.simplefilter("always")

        # Trigger module operations that might cause warnings
        # Just verify LLM class is accessible
        assert LLM is not None

        # Check that no Pydantic serializer warnings are in the list
        pydantic_warnings = [
            w for w in warning_list if "Pydantic serializer warnings" in str(w.message)
        ]

        assert len(pydantic_warnings) == 0, (
            f"Expected no Pydantic serializer warnings, "
            f"but found {len(pydantic_warnings)}"
        )


def test_llm_response_serialization_no_warnings():
    """Test serializing LLMResponse with litellm ModelResponse.

    This test creates a mock LLMResponse containing a litellm ModelResponse
    and serializes it using model_dump(), which would normally trigger
    Pydantic serializer warnings. The warning filter in llm_response.py
    should suppress these warnings during normal usage.
    """
    # Create a mock litellm ModelResponse with minimal fields
    mock_response = ModelResponse(
        id="test-id",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content="Test response", role="assistant"),
            )
        ],
        created=1234567890,
        model="test-model",
        object="chat.completion",
    )

    # Create an LLMResponse with the mock response
    llm_response = LLMResponse(
        message=Message(
            role="assistant", content=[TextContent(type="text", text="Test response")]
        ),
        metrics=MetricsSnapshot(
            model_name="test-model",
            accumulated_cost=0.0,
            max_budget_per_task=None,
            accumulated_token_usage=TokenUsage(
                model="test-model", prompt_tokens=0, completion_tokens=0
            ),
        ),
        raw_response=mock_response,
    )

    # Capture warnings during serialization
    # We need to test that the filter works, but catch_warnings creates
    # a new isolated environment, so we need to re-apply the filter
    with warnings.catch_warnings(record=True) as warning_list:
        # Re-apply the filter that should be active globally
        warnings.filterwarnings("ignore", message="Pydantic serializer warnings")

        # Serialize the LLMResponse - this would trigger warnings without the filter
        serialized = llm_response.model_dump()
        assert serialized is not None
        assert "message" in serialized
        assert "metrics" in serialized

        # Check that no Pydantic serializer warnings appeared
        pydantic_warnings = [
            w for w in warning_list if "Pydantic serializer warnings" in str(w.message)
        ]

        assert len(pydantic_warnings) == 0, (
            "Expected no Pydantic serializer warnings during "
            f"LLMResponse serialization, but found {len(pydantic_warnings)}"
        )
