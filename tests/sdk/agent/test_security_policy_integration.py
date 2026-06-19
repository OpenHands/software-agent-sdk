"""Test configurable security policy functionality."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event import ActionEvent, AgentErrorEvent
from openhands.sdk.llm import LLM, Message, TextContent


def test_security_policy_in_system_message():
    """Test that security policy is included in system message."""
    agent = Agent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        )
    )
    system_message = agent.static_system_message

    # Verify that security policy section is present
    assert "🔐 Security Policy" in system_message
    assert "OK to do without Explicit User Consent" in system_message
    assert "Do only with Explicit User Consent" in system_message
    assert "Never Do" in system_message

    # Verify specific policy items are present
    assert (
        "Download and run code from a repository specified by a user" in system_message
    )
    assert "Open pull requests on the original repositories" in system_message
    assert (
        "Install and run popular packages from **official** package registries"
        in system_message
    )
    assert (
        "Upload code to anywhere other than the location where it was obtained"
        in system_message
    )
    assert "Upload API keys or tokens anywhere" in system_message
    assert "Never perform any illegal activities" in system_message
    assert "Never run software to mine cryptocurrency" in system_message

    # Verify that all security guidelines are consolidated in the policy
    assert "General Security Guidelines" in system_message
    assert "Only use GITHUB_TOKEN and other credentials" in system_message
    assert "Use APIs to work with GitHub or other platforms" in system_message
    assert (
        "This [message/comment/issue/PR] was created by an AI agent" in system_message
    )
    assert "AI assistant (OpenHands)" not in system_message


def test_none_security_policy_filename_disables_policy_without_null_public_value():
    """Test that None input disables the policy without exposing a null contract."""
    agent = Agent.model_validate(
        {
            "llm": LLM(
                usage_id="test-llm",
                model="test-model",
                api_key=SecretStr("test-key"),
                base_url="http://test",
            ),
            "security_policy_filename": None,
        }
    )

    assert agent.security_policy_filename == ""
    assert agent.model_dump()["security_policy_filename"] == ""
    assert "🔐 Security Policy" not in agent.static_system_message


def test_custom_security_policy_in_system_message():
    """A custom security policy file's content is resolved into the system message
    via the registry (no template copying or Jinja escape hatch)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        custom_policy_path = Path(temp_dir) / "custom_policy.j2"
        custom_policy_content = (
            "# 🔐 Custom Test Security Policy\n"
            "This is a custom security policy for testing.\n"
            "- **CUSTOM_RULE**: Always test custom policies."
        )
        custom_policy_path.write_text(custom_policy_content, encoding="utf-8")

        agent = Agent(
            llm=LLM(
                usage_id="test-llm",
                model="test-model",
                api_key=SecretStr("test-key"),
                base_url="http://test",
            ),
            security_policy_filename=str(custom_policy_path),
        )

        system_message = agent.static_system_message

        # Custom policy content appears...
        assert "Custom Test Security Policy" in system_message
        assert "CUSTOM_RULE" in system_message
        assert "Always test custom policies" in system_message
        # ...and the built-in default policy does not leak in alongside it.
        assert "Download and run code from a repository" not in system_message


def test_custom_security_policy_is_inserted_verbatim_not_rendered():
    """Custom policy files are injected as raw text -- Jinja syntax is NOT evaluated.

    Intentional contract (a security policy should not be a template-injection
    surface); the legacy ``{% include %}`` path used to render it.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        policy_path = Path(temp_dir) / "custom_policy.j2"
        policy_path.write_text(
            "# Policy for {{ model_name }}\n- {% if cli_mode %}rule{% endif %}",
            encoding="utf-8",
        )
        agent = Agent(
            llm=LLM(
                usage_id="test-llm",
                model="test-model",
                api_key=SecretStr("test-key"),
                base_url="http://test",
            ),
            security_policy_filename=str(policy_path),
        )
        system_message = agent.static_system_message

        # Jinja is left literal, not evaluated.
        assert "# Policy for {{ model_name }}" in system_message
        assert "{% if cli_mode %}rule{% endif %}" in system_message


def test_empty_custom_security_policy_does_not_leak_default():
    """An explicitly empty custom policy file must not fall back to the default."""
    with tempfile.TemporaryDirectory() as temp_dir:
        empty_policy = Path(temp_dir) / "empty_policy.j2"
        empty_policy.write_text("", encoding="utf-8")
        agent = Agent(
            llm=LLM(
                usage_id="test-llm",
                model="test-model",
                api_key=SecretStr("test-key"),
                base_url="http://test",
            ),
            security_policy_filename=str(empty_policy),
        )
        system_message = agent.static_system_message

        assert "🔐 Security Policy" not in system_message
        assert "Download and run code from a repository" not in system_message


def test_llm_security_analyzer_template_kwargs():
    """Test that agent sets template_kwargs appropriately when security analyzer is LLMSecurityAnalyzer."""  # noqa: E501
    agent = Agent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        ),
    )

    # Get system message (security analyzer context is automatically included)
    system_message = agent.static_system_message

    # Verify that the security risk assessment section is included in the system prompt
    assert "<SECURITY_RISK_ASSESSMENT>" in system_message
    assert "# Security Risk Policy" in system_message
    assert "When using tools that support the security_risk parameter" in system_message
    # By default, cli_mode is True, so we should see the CLI mode version
    assert "**LOW**: Safe, read-only actions" in system_message
    assert "**MEDIUM**: Project-scoped edits or execution" in system_message
    assert "**HIGH**: System-level or untrusted operations" in system_message
    assert "**Global Rules**" in system_message


def test_llm_security_analyzer_sandbox_mode():
    """Test that agent includes sandbox mode security risk assessment when cli_mode=False."""  # noqa: E501
    # Create agent with cli_mode=False
    agent = Agent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        ),
        system_prompt_kwargs={"cli_mode": False},
    )

    # Get system message (security analyzer context is automatically included)
    system_message = agent.static_system_message

    print(agent.system_prompt_kwargs)

    # Verify that the security risk assessment section is included with sandbox mode content  # noqa: E501
    assert "<SECURITY_RISK_ASSESSMENT>" in system_message
    assert "# Security Risk Policy" in system_message
    assert "When using tools that support the security_risk parameter" in system_message
    # With cli_mode=False, we should see the sandbox mode version
    assert "**LOW**: Read-only actions inside sandbox" in system_message
    assert "**MEDIUM**: Container-scoped edits and installs" in system_message
    assert "**HIGH**: Data exfiltration or privilege breaks" in system_message
    assert "**Global Rules**" in system_message


def test_no_security_analyzer_still_includes_risk_assessment():
    """Test that security risk assessment section is excluded when no security analyzer is set."""  # noqa: E501
    # Create agent without security analyzer
    agent = Agent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        )
    )

    # Get the system message with no security analyzer
    system_message = agent.static_system_message

    # Verify that the security risk assessment section is NOT included
    assert "<SECURITY_RISK_ASSESSMENT>" in system_message
    assert "# Security Risk Policy" in system_message
    assert "When using tools that support the security_risk parameter" in system_message


def test_non_llm_security_analyzer_still_includes_risk_assessment():
    """Test that security risk assessment section is excluded when security analyzer is not LLMSecurityAnalyzer."""  # noqa: E501
    from openhands.sdk.security.analyzer import SecurityAnalyzerBase
    from openhands.sdk.security.risk import SecurityRisk

    class MockSecurityAnalyzer(SecurityAnalyzerBase):
        def security_risk(self, action: ActionEvent) -> SecurityRisk:
            return SecurityRisk.LOW

    # Create agent (security analyzer functionality has been deprecated and removed)
    agent = Agent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        ),
    )

    # Get the system message
    system_message = agent.static_system_message

    # Verify that the security risk assessment section is NOT included
    assert "<SECURITY_RISK_ASSESSMENT>" in system_message
    assert "# Security Risk Policy" in system_message
    assert "When using tools that support the security_risk parameter" in system_message


def _tool_response(name: str, args_json: str) -> ModelResponse:
    return ModelResponse(
        id="mock-response",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="tool call with security_risk",
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id="call_1",
                            type="function",
                            function=Function(name=name, arguments=args_json),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def test_security_risk_param_ignored_when_no_analyzer():
    """Security risk param is ignored when no analyzer is configured.

    This test reproduces the issue from #1957 where the LLM includes
    security_risk in tool calls even when llm_security_analyzer=False
    and no security analyzer is configured.

    Expected behavior: security_risk should be UNKNOWN when no analyzer is set.
    """
    from openhands.sdk.security.risk import SecurityRisk

    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    # Set llm_security_analyzer=False in system_prompt_kwargs
    agent = Agent(
        llm=llm, tools=[], system_prompt_kwargs={"llm_security_analyzer": False}
    )

    events = []
    convo = Conversation(agent=agent, callbacks=[events.append])

    # Mock LLM response that includes security_risk=HIGH even though
    # llm_security_analyzer=False (the LLM might do this if it's well-trained)
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_tool_response(
            "think",
            '{"thought": "This is a test thought", "security_risk": "HIGH"}',
        ),
    ):
        convo.send_message(
            Message(role="user", content=[TextContent(text="Please think")])
        )
        agent.step(convo, on_event=events.append)

    # No agent errors
    assert not any(isinstance(e, AgentErrorEvent) for e in events)

    # Find the ActionEvent
    action_events = [e for e in events if isinstance(e, ActionEvent)]
    assert len(action_events) == 1

    # Verify that the security_risk is UNKNOWN (ignored) when no analyzer is set
    # Even though the LLM provided "HIGH", it should be ignored
    assert action_events[0].security_risk == SecurityRisk.UNKNOWN
