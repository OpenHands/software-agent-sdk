"""Tests for LLMWithGateway custom header support."""

from __future__ import annotations

from unittest.mock import patch

from litellm.types.llms.openai import ResponseAPIUsage, ResponsesAPIResponse
from openai.types.responses.response_output_message import ResponseOutputMessage
from openai.types.responses.response_output_text import ResponseOutputText
from pydantic import SecretStr

from openhands.sdk.llm import LLMWithGateway, Message, TextContent
from tests.conftest import create_mock_litellm_response


def create_llm(custom_headers: dict[str, str] | None = None) -> LLMWithGateway:
    """Helper to build an LLMWithGateway for tests."""
    return LLMWithGateway(
        model="gemini-1.5-flash",
        api_key=SecretStr("test-api-key"),
        base_url="https://gateway.example.com/v1",
        custom_headers=custom_headers,
        usage_id="gateway-test-llm",
        num_retries=0,
    )


def make_responses_api_response(text: str) -> ResponsesAPIResponse:
    """Construct a minimal ResponsesAPIResponse for testing."""

    message = ResponseOutputMessage.model_construct(
        id="msg",
        type="message",
        role="assistant",
        status="completed",
        content=[  # type: ignore[arg-type]
            ResponseOutputText(type="output_text", text=text, annotations=[])
        ],
    )

    usage = ResponseAPIUsage(input_tokens=1, output_tokens=1, total_tokens=2)

    return ResponsesAPIResponse(
        id="resp",
        created_at=0,
        output=[message],  # type: ignore[arg-type]
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=usage,
        instructions=None,
        status="completed",
    )


class TestInitialization:
    """Basic initialization behaviour."""

    def test_defaults(self) -> None:
        llm = create_llm()
        assert llm.custom_headers is None

    def test_custom_headers_configuration(self) -> None:
        headers = {"X-Custom-Key": "value"}
        llm = create_llm(custom_headers=headers)
        assert llm.custom_headers == headers


class TestHeaderInjection:
    """Ensure custom headers are merged into completion calls."""

    @patch("openhands.sdk.llm.llm.litellm_completion")
    def test_headers_passed_to_litellm(self, mock_completion) -> None:
        llm = create_llm(custom_headers={"X-Test": "value"})
        mock_completion.return_value = create_mock_litellm_response(content="Hello!")

        messages = [Message(role="user", content=[TextContent(text="Hi")])]
        response = llm.completion(messages)

        mock_completion.assert_called_once()
        headers = mock_completion.call_args.kwargs["extra_headers"]
        assert headers["X-Test"] == "value"

        # Ensure we still surface the underlying content.
        content = response.message.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "Hello!"

    @patch("openhands.sdk.llm.llm.litellm_completion")
    def test_headers_merge_existing_extra_headers(self, mock_completion) -> None:
        llm = create_llm(custom_headers={"X-Test": "value"})
        mock_completion.return_value = create_mock_litellm_response(content="Merged!")

        messages = [Message(role="user", content=[TextContent(text="Hi")])]
        llm.completion(messages, extra_headers={"Existing": "1"})

        headers = mock_completion.call_args.kwargs["extra_headers"]
        assert headers["X-Test"] == "value"
        assert headers["Existing"] == "1"

    @patch("openhands.sdk.llm.llm.litellm_responses")
    def test_responses_headers_passed_to_litellm(self, mock_responses) -> None:
        llm = create_llm(custom_headers={"X-Test": "value"})
        mock_responses.return_value = make_responses_api_response("ok")

        llm.responses([Message(role="user", content=[TextContent(text="Hi")])])

        headers = mock_responses.call_args.kwargs["extra_headers"]
        assert headers["X-Test"] == "value"


class TestTemplateReplacement:
    """Template replacement should render against LLM fields."""

    def test_render_templates_string(self) -> None:
        llm = create_llm()
        template = "Model: {{llm_model}}, URL: {{llm_base_url}}"
        result = llm._render_templates(template)
        assert result == "Model: gemini-1.5-flash, URL: https://gateway.example.com/v1"

    def test_render_templates_dict(self) -> None:
        llm = create_llm()
        template = {
            "model": "{{llm_model}}",
            "endpoint": "{{llm_base_url}}/chat",
            "nested": {"key": "{{llm_model}}"},
        }
        result = llm._render_templates(template)
        assert result["model"] == "gemini-1.5-flash"
        assert result["endpoint"] == "https://gateway.example.com/v1/chat"
        assert result["nested"]["key"] == "gemini-1.5-flash"

    def test_render_templates_list(self) -> None:
        llm = create_llm()
        template = ["{{llm_model}}", {"url": "{{llm_base_url}}"}]
        result = llm._render_templates(template)
        assert result[0] == "gemini-1.5-flash"
        assert result[1]["url"] == "https://gateway.example.com/v1"

    def test_render_templates_with_api_key(self) -> None:
        llm = create_llm()
        template = "Key: {{llm_api_key}}"
        result = llm._render_templates(template)
        assert result == "Key: test-api-key"

    def test_render_templates_no_base_url(self) -> None:
        llm = LLMWithGateway(
            model="gpt-4",
            api_key=SecretStr("key"),
            usage_id="test",
        )
        template = "URL: {{llm_base_url}}"
        result = llm._render_templates(template)
        assert result == "URL: "
