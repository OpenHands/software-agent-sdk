"""Base class for API compliance tests."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from openhands.sdk import LLM
from openhands.sdk.llm import Message
from tests.integration.api_compliance.result import APIResponse, ComplianceTestResult


if TYPE_CHECKING:
    from openhands.sdk.tool import ToolDefinition


def get_minimal_tool_definitions() -> "Sequence[ToolDefinition[Any, Any]]":
    """Create minimal tool definitions for tests that need tool calling."""
    from openhands.sdk.llm import TextContent
    from openhands.sdk.tool import Action, Observation, ToolDefinition

    class ComplianceTestAction(Action):
        """Minimal action for compliance testing."""

        command: str

    class ComplianceTestObservation(Observation):
        """Minimal observation for compliance testing."""

        result: str

        @property
        def to_llm_content(self) -> list[TextContent]:
            return [TextContent(text=self.result)]

    # Create a minimal ToolDefinition directly
    class ComplianceTestTool(
        ToolDefinition[ComplianceTestAction, ComplianceTestObservation]
    ):
        """Minimal tool for API compliance tests."""

        @classmethod
        def create(cls, *args: Any, **kwargs: Any) -> "Sequence[ComplianceTestTool]":
            return [
                cls(
                    description="Execute a terminal command",
                    action_type=ComplianceTestAction,
                    observation_type=ComplianceTestObservation,
                )
            ]

    return ComplianceTestTool.create()


class BaseAPIComplianceTest(ABC):
    """Base class for API compliance tests.

    Subclasses must implement:
    - pattern_name: Unique identifier for the pattern
    - pattern_description: Human-readable description
    - build_malformed_messages(): Returns list of Message objects representing
      the malformed conversation

    The test framework will call run_test() with different LLM configurations
    to see how each provider responds to the malformed input.
    """

    @property
    @abstractmethod
    def pattern_name(self) -> str:
        """Unique identifier for the malformed pattern being tested."""
        pass

    @property
    @abstractmethod
    def pattern_description(self) -> str:
        """Human-readable description of the malformed pattern."""
        pass

    @abstractmethod
    def build_malformed_messages(self) -> list[Message]:
        """Construct the malformed message sequence to send to the API.

        Returns:
            List of Message objects representing the malformed conversation.
        """
        pass

    def needs_tools(self) -> bool:
        """Whether this test needs tool definitions sent to the API.

        Override to return False if the test doesn't need tools.
        Most tests involving tool_use/tool_result need tools defined.
        """
        return True

    def get_tool_definitions(self) -> "Sequence[ToolDefinition[Any, Any]]":
        """Get tool definitions to send with the request.

        Override to customize tool definitions.
        """
        return get_minimal_tool_definitions()

    def run_test(
        self,
        llm: LLM,
    ) -> ComplianceTestResult:
        """Execute the test against the given LLM and record results.

        Args:
            llm: LLM instance to test against

        Returns:
            ComplianceTestResult with the outcome
        """
        messages = self.build_malformed_messages()
        provider = self._extract_provider(llm.model)

        tools = self.get_tool_definitions() if self.needs_tools() else None

        try:
            response = llm.completion(
                messages=messages,
                tools=tools,
            )
            # If we get here, the API accepted the malformed input
            return ComplianceTestResult(
                pattern_name=self.pattern_name,
                model=llm.model,
                provider=provider,
                response_type=APIResponse.ACCEPTED,
                raw_response=response.raw_response.model_dump()
                if response.raw_response
                else None,
                notes="API accepted malformed input (unexpected)",
            )
        except TimeoutError as e:
            return ComplianceTestResult(
                pattern_name=self.pattern_name,
                model=llm.model,
                provider=provider,
                response_type=APIResponse.TIMEOUT,
                error_message=str(e),
                error_type=type(e).__name__,
            )
        except ConnectionError as e:
            return ComplianceTestResult(
                pattern_name=self.pattern_name,
                model=llm.model,
                provider=provider,
                response_type=APIResponse.CONNECTION_ERROR,
                error_message=str(e),
                error_type=type(e).__name__,
            )
        except Exception as e:
            # Extract HTTP status if available
            http_status = None
            error_str = str(e)
            # Check for status_code attribute (common in HTTP exceptions)
            status_code_attr = getattr(e, "status_code", None)
            if isinstance(status_code_attr, int):
                http_status = status_code_attr
            elif "status_code" in error_str:
                # Try to parse from error message
                import re

                match = re.search(r"status_code[=:\s]*(\d+)", error_str)
                if match:
                    http_status = int(match.group(1))

            return ComplianceTestResult(
                pattern_name=self.pattern_name,
                model=llm.model,
                provider=provider,
                response_type=APIResponse.REJECTED,
                error_message=str(e),
                error_type=type(e).__name__,
                http_status=http_status,
            )

    def _extract_provider(self, model: str) -> str:
        """Extract provider name from model string."""
        model_lower = model.lower()
        if "claude" in model_lower or "anthropic" in model_lower:
            return "anthropic"
        elif "gpt" in model_lower or "openai" in model_lower:
            return "openai"
        elif "gemini" in model_lower or "google" in model_lower:
            return "google"
        elif "deepseek" in model_lower:
            return "deepseek"
        elif "kimi" in model_lower or "moonshot" in model_lower:
            return "moonshot"
        elif "qwen" in model_lower or "dashscope" in model_lower:
            return "alibaba"
        elif "glm" in model_lower:
            return "zhipu"
        elif "minimax" in model_lower:
            return "minimax"
        else:
            # Return the first part of the model name
            return model.split("/")[0] if "/" in model else "unknown"


def create_test_llm(llm_config: dict[str, Any]) -> LLM:
    """Create an LLM instance for compliance testing.

    Args:
        llm_config: LLM configuration dict (model, temperature, etc.)

    Returns:
        Configured LLM instance
    """
    import os

    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL")

    if not api_key:
        raise ValueError("LLM_API_KEY environment variable not set")

    return LLM(
        **llm_config,
        api_key=SecretStr(api_key),
        base_url=base_url,
        timeout=60,  # Short timeout for compliance tests
        num_retries=0,  # No retries - we want to see the raw error
    )
