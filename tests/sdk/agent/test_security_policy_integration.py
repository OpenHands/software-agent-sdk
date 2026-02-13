"""Test configurable security policy functionality."""

import shutil
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
    assert "Install and run popular packages from pypi, npm" in system_message
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


def test_custom_security_policy_in_system_message():
    """Test that custom security policy filename is used in system message."""
    # Create a temporary directory for test files
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a custom policy file with distinctive content
        custom_policy_path = Path(temp_dir) / "custom_policy.j2"
        custom_policy_content = (
            "# 🔐 Custom Test Security Policy\n"
            "This is a custom security policy for testing.\n"
            "- **CUSTOM_RULE**: Always test custom policies."
        )
        custom_policy_path.write_text(custom_policy_content)

        # Copy required template files to temp directory
        original_prompt_dir = (
            Path(__file__).parent.parent.parent.parent
            / "openhands-sdk"
            / "openhands"
            / "sdk"
            / "agent"
            / "prompts"
        )

        # Copy system_prompt.j2
        system_prompt_path = Path(temp_dir) / "system_prompt.j2"
        original_system_prompt = original_prompt_dir / "system_prompt.j2"
        shutil.copy2(original_system_prompt, system_prompt_path)

        # Copy security_risk_assessment.j2
        security_risk_assessment_path = Path(temp_dir) / "security_risk_assessment.j2"
        original_security_risk_assessment = (
            original_prompt_dir / "security_risk_assessment.j2"
        )
        shutil.copy2(original_security_risk_assessment, security_risk_assessment_path)

        # Copy self_documentation.j2
        self_documentation_path = Path(temp_dir) / "self_documentation.j2"
        original_self_documentation = original_prompt_dir / "self_documentation.j2"
        shutil.copy2(original_self_documentation, self_documentation_path)

        # Create agent with custom security policy using absolute paths for both
        agent = Agent(
            llm=LLM(
                usage_id="test-llm",
                model="test-model",
                api_key=SecretStr("test-key"),
                base_url="http://test",
            ),
            system_prompt_filename=str(system_prompt_path),
            security_policy_filename=str(custom_policy_path),
        )

        # Get system message - this should include our custom policy
        system_message = agent.static_system_message

        # Verify that custom policy content appears in system message
        assert "Custom Test Security Policy" in system_message
        assert "CUSTOM_RULE" in system_message
        assert "Always test custom policies" in system_message


def test_security_policy_template_rendering():
    """Test that the security policy template renders correctly."""

    from openhands.sdk.context.prompts.prompt import render_template

    # Get the prompts directory
    agent = Agent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        )
    )
    prompt_dir = agent.prompt_dir

    # Render the security policy template
    security_policy = render_template(prompt_dir, "security_policy.j2")

    # Verify the content structure
    assert security_policy.startswith("# 🔐 Security Policy")
    assert "## OK to do without Explicit User Consent" in security_policy
    assert "## Do only with Explicit User Consent" in security_policy
    assert "## Never Do" in security_policy

    # Verify it's properly formatted (no extra whitespace at start/end)
    assert not security_policy.startswith(" ")
    assert not security_policy.endswith(" ")


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
    """Security risk param is ignored when no analyzer is configured."""

    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[])

    events = []
    convo = Conversation(agent=agent, callbacks=[events.append])

    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_tool_response(
            "think",
            '{"thought": "This is a test thought", "security_risk": "LOW"}',
        ),
    ):
        convo.send_message(
            Message(role="user", content=[TextContent(text="Please think")])
        )
        agent.step(convo, on_event=events.append)

    # No agent errors
    assert not any(isinstance(e, AgentErrorEvent) for e in events)
