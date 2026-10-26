"""Forked conversations retain the source's configured stopping behavior."""

import json

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM


@pytest.mark.parametrize(
    "thresholds,pattern,enabled,expected_count",
    [
        ({"action_observation": 2}, "identical", True, 2),
        ({"action_observation": 6, "alternating_pattern": 8}, "identical", True, 6),
        ({"action_error": 2, "action_observation": 8}, "error", True, 3),
        ({"monologue": 2}, "monologue", True, 2),
        ({"alternating_pattern": 4}, "alternating", True, 4),
        ({}, "identical", True, 4),
        ({"action_observation": 2}, "identical", False, 7),
    ],
)
def test_fork_preserves_native_stopping_behavior(
    thresholds, pattern, enabled, expected_count, monkeypatch, tmp_path
):
    source = Conversation(
        agent=Agent(llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test"))),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "conversations"),
        stuck_detection=enabled,
        stuck_detection_thresholds=thresholds,
    )
    source.send_message("Start")
    fork = source.fork()

    messages: list[Message | Exception] = []
    for i in range(7):
        message = Message(role="assistant", content=[])
        if pattern != "monologue":
            arguments = (
                {}
                if pattern == "error"
                else {"thought": str(i % 2 if pattern == "alternating" else 0)}
            )
            message.tool_calls = [
                MessageToolCall(
                    id=f"call_{i}",
                    name="think",
                    arguments=json.dumps(arguments),
                    origin="completion",
                )
            ]
        messages.append(message)
    messages.append(Message(role="assistant", content=[TextContent(text="Done.")]))

    for conversation in [source, fork]:
        scripted_llm = TestLLM.from_messages(messages)
        # Fork round-trips the agent through JSON. Stub the LLM boundary so
        # both independently reconstructed agents receive the same responses.
        monkeypatch.setattr(
            LLM,
            "completion",
            lambda self, *args, **kwargs: scripted_llm.completion(*args, **kwargs),
        )
        conversation.run()
        assert conversation.state.execution_status == (
            ConversationExecutionStatus.STUCK
            if enabled
            else ConversationExecutionStatus.FINISHED
        )
        assert (
            sum(
                isinstance(event, ActionEvent)
                or (
                    pattern == "monologue"
                    and isinstance(event, MessageEvent)
                    and event.source == "agent"
                )
                for event in conversation.state.events
            )
            == expected_count
        )
