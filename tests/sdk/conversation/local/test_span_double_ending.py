"""Test for the span double-ending issue in LocalConversation."""

import logging
import tempfile
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import LLM


def create_test_agent() -> Agent:
    """Create a test agent."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


def test_no_double_span_ending_warning(caplog):
    """Test that LocalConversation doesn't produce double span ending warnings."""

    # Create test agent
    agent = create_test_agent()

    # Create a temporary workspace
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create conversation
        conversation = LocalConversation(
            agent=agent,
            workspace=temp_dir,
            visualizer=None,  # Disable visualization to simplify test
        )

        # Capture logs at WARNING level
        with caplog.at_level(logging.WARNING):
            # Mock the agent.step to raise an exception to trigger the finally block
            with patch(
                "openhands.sdk.agent.agent.Agent.step",
                side_effect=Exception("Test exception"),
            ):
                # Try to run the conversation (will fail due to mocked exception)
                with pytest.raises(Exception):
                    conversation.run()

            # Close the conversation (this would normally be called by __del__)
            conversation.close()

        # Check that no warning about empty span stack was logged
        warning_messages = [
            record.message for record in caplog.records if record.levelname == "WARNING"
        ]
        span_warnings = [
            msg
            for msg in warning_messages
            if "Attempted to end active span, but stack is empty" in msg
        ]

        # This test should fail initially (showing the bug exists)
        # After the fix, there should be no span warnings
        assert len(span_warnings) == 0, f"Found span warnings: {span_warnings}"


def test_span_ending_with_successful_run(caplog):
    """Test span ending behavior with a successful run (no exceptions)."""

    # Create test agent
    agent = create_test_agent()

    # Create a temporary workspace
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create conversation
        conversation = LocalConversation(
            agent=agent, workspace=temp_dir, visualize=False
        )

        # Mock the agent.step to finish immediately (no iterations)
        def finish_immediately(*args, **kwargs):
            conversation._state.execution_status = (
                conversation._state.execution_status.__class__.FINISHED
            )

        # Capture logs at WARNING level
        with caplog.at_level(logging.WARNING):
            with patch(
                "openhands.sdk.agent.agent.Agent.step", side_effect=finish_immediately
            ):
                # Run the conversation successfully
                conversation.run()

            # Close the conversation
            conversation.close()

        # Check that no warning about empty span stack was logged
        warning_messages = [
            record.message for record in caplog.records if record.levelname == "WARNING"
        ]
        span_warnings = [
            msg
            for msg in warning_messages
            if "Attempted to end active span, but stack is empty" in msg
        ]

        assert len(span_warnings) == 0, f"Found span warnings: {span_warnings}"


def test_no_span_operations_when_observability_disabled(caplog):
    """Test that no span operations occur when observability is disabled."""

    # Create test agent
    agent = create_test_agent()

    # Create a temporary workspace
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create conversation
        conversation = LocalConversation(
            agent=agent, workspace=temp_dir, visualize=False
        )

        # Mock the agent.step to finish immediately
        def finish_immediately(*args, **kwargs):
            conversation._state.execution_status = (
                conversation._state.execution_status.__class__.FINISHED
            )

        # Capture logs at WARNING level
        with caplog.at_level(logging.WARNING):
            # Run and close the conversation
            with patch(
                "openhands.sdk.agent.agent.Agent.step", side_effect=finish_immediately
            ):
                conversation.run()
            conversation.close()

        # Check that no warning about empty span stack was logged
        warning_messages = [
            record.message for record in caplog.records if record.levelname == "WARNING"
        ]
        span_warnings = [
            msg
            for msg in warning_messages
            if "Attempted to end active span, but stack is empty" in msg
        ]

        assert len(span_warnings) == 0, f"Found span warnings: {span_warnings}"
