"""Tests for absolute path support in system_prompt_filename."""

import os
import tempfile

import pytest
from pydantic import SecretStr

from openhands.sdk.agent.agent import Agent
from openhands.sdk.context.prompts.prompt import render_template
from openhands.sdk.llm import LLM


def test_render_template_with_relative_path():
    """Test that render_template works with relative paths (existing behavior)."""
    # Use the agent's default prompts directory
    agent_prompts_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "../openhands-sdk/openhands/sdk/agent/prompts",
    )
    agent_prompts_dir = os.path.abspath(agent_prompts_dir)

    # Render a template using relative path
    result = render_template(
        prompt_dir=agent_prompts_dir,
        template_name="system_prompt.j2",
        cli_mode=False,
        security_policy_filename="security_policy.j2",
    )

    # Verify result is a non-empty string
    assert isinstance(result, str)
    assert len(result) > 0


def test_render_template_with_absolute_path():
    """Test that render_template works with absolute paths."""
    # Create a temporary template file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".j2", delete=False) as tmp_file:
        tmp_file.write("Hello {{ name }}! This is a test template.")
        tmp_path = tmp_file.name

    try:
        # Render using absolute path
        result = render_template(
            prompt_dir="/unused/dir",  # This should be ignored for absolute paths
            template_name=tmp_path,
            name="World",
        )

        assert result == "Hello World! This is a test template."
    finally:
        # Clean up
        os.unlink(tmp_path)


def test_agent_with_absolute_system_prompt_path():
    """Test that Agent can use an absolute path for system_prompt_filename."""
    # Create a temporary template file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".j2", delete=False) as tmp_file:
        tmp_file.write(
            "You are a test assistant. CLI mode: {{ cli_mode|default(false) }}"
        )
        tmp_path = tmp_file.name

    try:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )

        # Create agent with absolute path to system prompt
        agent = Agent(
            llm=llm,
            tools=[],
            system_prompt_filename=tmp_path,
            system_prompt_kwargs={"cli_mode": True},
        )

        # Get system message
        system_message = agent.static_system_message

        # Verify the message was rendered correctly
        assert "You are a test assistant" in system_message
        assert "CLI mode: True" in system_message
    finally:
        # Clean up
        os.unlink(tmp_path)


def test_agent_with_relative_system_prompt_path():
    """Test that Agent still works with relative paths (backward compatibility)."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")

    # Create agent with default relative path
    agent = Agent(
        llm=llm,
        tools=[],
        system_prompt_filename="system_prompt.j2",  # Relative path
    )

    # Get system message
    system_message = agent.static_system_message

    # Verify the message was rendered correctly
    assert isinstance(system_message, str)
    assert len(system_message) > 0


def test_render_template_with_nonexistent_absolute_path():
    """Test render_template raises error for nonexistent absolute path."""  # noqa: E501
    nonexistent_path = "/nonexistent/directory/template.j2"

    with pytest.raises(FileNotFoundError, match="Prompt file"):
        render_template(
            prompt_dir="/unused/dir",
            template_name=nonexistent_path,
            name="Test",
        )


def test_render_template_with_nonexistent_relative_path():
    """Test render_template raises error for nonexistent relative path."""  # noqa: E501
    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.raises(FileNotFoundError, match="Prompt file"):
            render_template(
                prompt_dir=tmp_dir,
                template_name="nonexistent_template.j2",
                name="Test",
            )
