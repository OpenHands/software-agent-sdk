"""Tests for process management guidance in system prompt."""

from openhands.sdk.agent import Agent
from openhands.sdk.llm import LLM


def test_system_prompt_includes_wait_for_background_process_guidance() -> None:
    """Test that system prompt includes guidance on waiting for background processes."""
    llm = LLM(model="gpt-4", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    message = agent.system_message

    # Verify the guidance about waiting for background processes is included
    assert "tail --pid=$PID -f output.log" in message
    assert "Capture its PID with `PID=$!`" in message
    assert "When waiting for background processes to finish" in message
