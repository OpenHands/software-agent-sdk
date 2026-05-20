"""Tests for sub-agent event forwarding via the parent callback chain.

When a sub-agent is spawned by the DelegateExecutor, its events should be
forwarded through the parent conversation's callback chain with a
``subagent_id`` tag.  The parent's internal ``_default_callback`` must skip
those events so they are **not** persisted in the parent's event log, but
external callbacks (e.g. PubSub → WebSocket) still receive them.

These tests use *real* ``LocalConversation`` sub-agents backed by ``TestLLM``
(scripted to call ``finish`` immediately) so the forwarding callback actually
fires during the agent loop.
"""

import json

import pytest

from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
    register_agent,
)
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool
from openhands.tools.delegate import DelegateExecutor
from openhands.tools.delegate.definition import DelegateAction
from openhands.tools.preset import register_builtins_agents


# Counter for unique agent IDs across factory invocations.
_factory_counter = 0


def _finish_messages(agent_id: str) -> list[Message | Exception]:
    """TestLLM script: immediately call finish."""
    return [
        Message(
            role="assistant",
            content=[TextContent(text="")],
            tool_calls=[
                MessageToolCall(
                    id=f"call_finish_{agent_id}",
                    name="finish",
                    arguments=json.dumps({"message": f"done_{agent_id}"}),
                    origin="completion",
                ),
            ],
        ),
    ]


def _finish_agent_factory(llm: LLM) -> Agent:
    """Factory that creates a minimal agent (finish-only, no terminal).

    Each invocation gets an independent ``TestLLM`` so sub-agents don't
    share a response queue.
    """
    global _factory_counter
    idx = _factory_counter
    _factory_counter += 1
    test_llm = TestLLM.from_messages(_finish_messages(f"agent_{idx}"))
    return Agent(llm=test_llm, tools=[])


@pytest.fixture(autouse=True)
def _clean_registry():
    global _factory_counter
    _factory_counter = 0
    _reset_registry_for_tests()
    register_builtins_agents()
    register_agent(
        name="finisher",
        factory_func=_finish_agent_factory,
        description="Agent that calls finish immediately",
    )
    yield
    _reset_registry_for_tests()


class TestSubagentEventForwarding:
    def test_subagent_events_carry_subagent_id(self, tmp_path):
        """Events produced by a sub-agent should have subagent_id set when
        they arrive at the parent's callback."""
        parent_llm = TestLLM.from_messages([])
        parent_agent = Agent(llm=parent_llm, tools=[Tool(name="delegate")])
        parent = LocalConversation(agent=parent_agent, workspace=str(tmp_path))

        received_events: list[Event] = []
        original_cb = parent._on_event

        def _spy(event: Event) -> None:
            received_events.append(event)
            original_cb(event)

        parent._on_event = _spy

        executor = DelegateExecutor(max_children=5)
        executor(
            DelegateAction(command="spawn", ids=["sub1"], agent_types=["finisher"]),
            parent,
        )
        executor(
            DelegateAction(command="delegate", tasks={"sub1": "do stuff"}),
            parent,
        )

        tagged = [e for e in received_events if e.subagent_id == "sub1"]
        assert len(tagged) > 0, (
            f"Expected forwarded events with subagent_id='sub1', "
            f"got {len(received_events)} events total, none tagged"
        )

    def test_subagent_events_not_in_parent_event_log(self, tmp_path):
        """Sub-agent events must NOT appear in the parent conversation's
        event log — only in the external callback stream."""
        parent_llm = TestLLM.from_messages([])
        parent_agent = Agent(llm=parent_llm, tools=[Tool(name="delegate")])
        parent_conv = LocalConversation(agent=parent_agent, workspace=str(tmp_path))

        external_events: list[Event] = []
        original_cb = parent_conv._on_event

        def _combined(event: Event) -> None:
            external_events.append(event)
            original_cb(event)

        parent_conv._on_event = _combined

        executor = DelegateExecutor(max_children=5)
        executor(
            DelegateAction(command="spawn", ids=["sub1"], agent_types=["finisher"]),
            parent_conv,
        )
        executor(
            DelegateAction(command="delegate", tasks={"sub1": "do stuff"}),
            parent_conv,
        )

        tagged_external = [e for e in external_events if e.subagent_id == "sub1"]
        assert len(tagged_external) > 0

        parent_events = list(parent_conv.state.events)
        tagged_parent = [e for e in parent_events if e.subagent_id is not None]
        assert tagged_parent == [], (
            f"Parent event log should not contain subagent events, "
            f"found {len(tagged_parent)}"
        )

    def test_multiple_subagents_have_distinct_ids(self, tmp_path):
        """Events from different sub-agents should carry their respective IDs."""
        parent_llm = TestLLM.from_messages([])
        parent_agent = Agent(llm=parent_llm, tools=[Tool(name="delegate")])
        parent = LocalConversation(agent=parent_agent, workspace=str(tmp_path))

        received_events: list[Event] = []
        original_cb = parent._on_event

        def _spy(event: Event) -> None:
            received_events.append(event)
            original_cb(event)

        parent._on_event = _spy

        executor = DelegateExecutor(max_children=5)
        executor(
            DelegateAction(
                command="spawn",
                ids=["alpha", "beta"],
                agent_types=["finisher", "finisher"],
            ),
            parent,
        )
        executor(
            DelegateAction(
                command="delegate",
                tasks={"alpha": "task a", "beta": "task b"},
            ),
            parent,
        )

        alpha_events = [e for e in received_events if e.subagent_id == "alpha"]
        beta_events = [e for e in received_events if e.subagent_id == "beta"]
        assert len(alpha_events) > 0
        assert len(beta_events) > 0

    def test_original_events_have_no_subagent_id(self):
        """Events produced by the parent agent itself must have
        subagent_id=None (the default)."""
        event = MessageEvent(
            source="user",
            llm_message=Message(role="user"),
        )
        assert event.subagent_id is None
