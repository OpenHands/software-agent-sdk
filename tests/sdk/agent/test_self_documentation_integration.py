"""Test that the self-documentation section is properly integrated into the
agent system prompt."""

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.context.prompts.prompt import render_template
from openhands.sdk.llm import LLM


def test_self_documentation_in_system_message():
    """Test that the self-documentation section is included in the agent's
    system message."""
    # Create a minimal agent configuration
    agent = Agent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        )
    )

    # Get the system message
    system_message = agent.system_message

    # Verify that the self-documentation content is included
    assert "<SELF_DOCUMENTATION>" in system_message
    assert "When the user directly asks about any of the following" in system_message
    assert "https://docs.all-hands.dev/" in system_message

    # Verify key trigger conditions are present
    assert "OpenHands capabilities" in system_message
    assert "what you're able to do in second person" in system_message
    assert "how to use a specific OpenHands feature or product" in system_message
    assert "OpenHands SDK, CLI, GUI, or other OpenHands products" in system_message

    # Verify all OpenHands products are mentioned
    assert "OpenHands SDK" in system_message
    assert "OpenHands CLI" in system_message
    assert "OpenHands GUI" in system_message
    assert "OpenHands Cloud" in system_message
    assert "OpenHands Enterprise" in system_message

    # Verify fetch tool instruction and link guidance
    assert "Use the fetch tool" in system_message
    assert "provide links to the relevant documentation pages" in system_message


def test_self_documentation_template_rendering():
    """Test that the self-documentation template renders correctly."""
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

    # Render the self-documentation template
    self_documentation = render_template(prompt_dir, "self_documentation.j2")

    # Verify the content structure
    assert self_documentation.startswith(
        "When the user directly asks about any of the following"
    )

    # Verify it's properly formatted (no extra whitespace at start/end)
    assert not self_documentation.startswith(" ")
    assert not self_documentation.endswith(" ")

    # Verify key elements are present
    assert "Use the fetch tool" in self_documentation
    assert "https://docs.all-hands.dev/" in self_documentation
    assert "OpenHands SDK" in self_documentation
    assert "OpenHands CLI" in self_documentation
    assert "OpenHands GUI" in self_documentation
    assert "OpenHands Cloud" in self_documentation
    assert "OpenHands Enterprise" in self_documentation
    assert "provide links to the relevant documentation pages" in self_documentation
