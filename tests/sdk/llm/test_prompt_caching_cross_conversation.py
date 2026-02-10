"""Regression test: static system message must be constant across conversations.

This test prevents accidental introduction of dynamic content into the static
system prompt, which would break cross-conversation prompt caching.

For prompt caching to work across conversations, the system message must be
identical for all conversations regardless of per-conversation context.
"""

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.context.skills import Skill
from openhands.sdk.llm import Message, TextContent


def test_static_system_message_is_constant_across_different_contexts():
    """REGRESSION TEST: Static system message must be identical regardless of context.

    If this test fails, it means dynamic content has been accidentally included
    in the static system message, which will break cross-conversation prompt caching.

    The static_system_message property should return the exact same string for all
    agents, regardless of what AgentContext they are configured with.
    """
    llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("fake-key"),
        usage_id="test",
    )

    # Create agents with vastly different contexts to stress-test the separation
    contexts = [
        None,
        AgentContext(system_message_suffix="User: alice"),
        AgentContext(system_message_suffix="User: bob\nRepo: project-x"),
        AgentContext(
            system_message_suffix="Complex context with lots of info",
            skills=[
                Skill(name="test-skill", content="Test skill content", trigger=None)
            ],
        ),
        AgentContext(
            system_message_suffix="Hosts:\n- host1.example.com\n- host2.example.com",
        ),
        AgentContext(
            system_message_suffix="Working directory: /some/path\nDate: 2024-01-15",
        ),
    ]

    agents = [Agent(llm=llm, agent_context=ctx) for ctx in contexts]

    # All static system messages must be identical
    first_static_message = agents[0].static_system_message

    for i, agent in enumerate(agents[1:], 1):
        assert agent.static_system_message == first_static_message, (
            f"Agent {i} has different static_system_message!\n"
            f"This breaks cross-conversation cache sharing.\n"
            f"Context: {contexts[i]}"
        )


@pytest.mark.parametrize(
    ("dynamic_context", "expect_dynamic"),
    [
        (TextContent(text="Dynamic context"), True),
        (None, False),
    ],
)
def test_end_to_end_caching_flow(tmp_path, dynamic_context, expect_dynamic):
    """Integration test: init_state → events_to_messages → caching.

    Verifies the system prompt is emitted with the correct number of blocks and
    that caching marks the static block (and the last user block) only.
    """
    import uuid

    from openhands.sdk.conversation import ConversationState
    from openhands.sdk.event import MessageEvent, SystemPromptEvent
    from openhands.sdk.event.base import LLMConvertibleEvent
    from openhands.sdk.workspace import LocalWorkspace

    llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("fake-key"),
        usage_id="test",
        caching_prompt=True,
    )

    context = None
    if dynamic_context is not None:
        context = AgentContext(system_message_suffix=dynamic_context.text)

    agent = Agent(llm=llm, agent_context=context)

    workspace = LocalWorkspace(working_dir=str(tmp_path))
    state = ConversationState.create(
        id=uuid.uuid4(),
        workspace=workspace,
        persistence_dir=str(tmp_path / ".state"),
        agent=agent,
    )

    collected_events: list = []

    def on_event(event):
        collected_events.append(event)
        state.events.append(event)

    agent.init_state(state, on_event=on_event)

    assert len(collected_events) == 1
    system_event = collected_events[0]
    assert isinstance(system_event, SystemPromptEvent)
    assert (system_event.dynamic_context is not None) is expect_dynamic

    user_message = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text="Hello")],
        ),
    )
    state.events.append(user_message)

    llm_convertible_events = [
        e for e in state.events if isinstance(e, LLMConvertibleEvent)
    ]
    messages = LLMConvertibleEvent.events_to_messages(llm_convertible_events)

    assert len(messages) == 2
    assert messages[0].role == "system"
    expected_blocks = 2 if expect_dynamic else 1
    assert len(messages[0].content) == expected_blocks
    assert messages[0].content[0].cache_prompt is False

    llm._apply_prompt_caching(messages)

    assert messages[0].content[0].cache_prompt is True
    if expect_dynamic:
        assert messages[0].content[1].cache_prompt is False
    assert messages[1].content[-1].cache_prompt is True


@pytest.mark.parametrize(
    ("first_suffix", "second_suffix"),
    [
        ("User: alice\nRepo: project-a", "User: bob\nRepo: project-b"),
        ("Working directory: /a", "Working directory: /b"),
    ],
)
def test_cross_conversation_cache_sharing(tmp_path, first_suffix, second_suffix):
    """Two conversations should share identical static prompts and cache marks."""
    import uuid

    from openhands.sdk.conversation import ConversationState
    from openhands.sdk.event import MessageEvent, SystemPromptEvent
    from openhands.sdk.event.base import LLMConvertibleEvent
    from openhands.sdk.workspace import LocalWorkspace

    llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("fake-key"),
        usage_id="test",
        caching_prompt=True,
    )

    static_prompts = []
    dynamic_contexts = []

    for index, suffix in enumerate((first_suffix, second_suffix)):
        agent = Agent(llm=llm, agent_context=AgentContext(system_message_suffix=suffix))

        conv_dir = tmp_path / f"conv_{index}"
        conv_dir.mkdir()
        workspace = LocalWorkspace(working_dir=str(conv_dir))
        state = ConversationState.create(
            id=uuid.uuid4(),
            workspace=workspace,
            persistence_dir=str(conv_dir / ".state"),
            agent=agent,
        )

        collected_events: list = []

        def on_event(event):
            collected_events.append(event)
            state.events.append(event)

        agent.init_state(state, on_event=on_event)

        system_event = collected_events[0]
        assert isinstance(system_event, SystemPromptEvent)

        user_message = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Hi")],
            ),
        )
        state.events.append(user_message)

        llm_convertible_events = [
            e for e in state.events if isinstance(e, LLMConvertibleEvent)
        ]
        messages = LLMConvertibleEvent.events_to_messages(llm_convertible_events)
        llm._apply_prompt_caching(messages)

        static_block = messages[0].content[0]
        dynamic_block = messages[0].content[1]
        assert isinstance(static_block, TextContent)
        assert isinstance(dynamic_block, TextContent)
        static_prompts.append(static_block.text)
        dynamic_contexts.append(dynamic_block.text)

        assert static_block.cache_prompt is True
        assert dynamic_block.cache_prompt is False

    assert static_prompts[0] == static_prompts[1]
    assert dynamic_contexts[0] != dynamic_contexts[1]
