"""Regression test: static system message must be constant across conversations.

This test prevents accidental introduction of dynamic content into the static
system prompt, which would break cross-conversation prompt caching.

For prompt caching to work across conversations, the system message must be
identical for all conversations regardless of per-conversation context.
"""

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


def test_apply_prompt_caching_marks_static_block_only():
    """LLM._apply_prompt_caching() should mark only the static block (index 0).

    When there are two system content blocks (static + dynamic), only the first
    block should be marked with cache_prompt=True. The second block (dynamic)
    should have cache_prompt=False to enable cross-conversation cache sharing.
    """
    llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("fake-key"),
        usage_id="test",
        caching_prompt=True,
    )

    # Create messages with two-block system message structure
    messages = [
        Message(
            role="system",
            content=[
                TextContent(text="Static system prompt"),
                TextContent(text="Dynamic context"),
            ],
        ),
        Message(
            role="user",
            content=[TextContent(text="Hello")],
        ),
    ]

    # Apply prompt caching
    llm._apply_prompt_caching(messages)

    # Verify static block is marked, dynamic block is not
    assert messages[0].content[0].cache_prompt is True
    assert messages[0].content[1].cache_prompt is False

    # User message's last content should also be marked
    assert messages[1].content[-1].cache_prompt is True


def test_apply_prompt_caching_single_block():
    """LLM._apply_prompt_caching() should mark single system block."""
    llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("fake-key"),
        usage_id="test",
        caching_prompt=True,
    )

    # Create messages with single-block system message
    messages = [
        Message(
            role="system",
            content=[TextContent(text="System prompt")],
        ),
        Message(
            role="user",
            content=[TextContent(text="Hello")],
        ),
    ]

    # Apply prompt caching
    llm._apply_prompt_caching(messages)

    # Verify single block is marked
    assert messages[0].content[0].cache_prompt is True

    # User message's last content should also be marked
    assert messages[1].content[-1].cache_prompt is True


def test_end_to_end_caching_flow_with_init_state(tmp_path):
    """Integration test: init_state() → events_to_messages() → caching flow.

    This test verifies the complete end-to-end flow for cross-conversation
    prompt caching:
    1. Agent.init_state() creates SystemPromptEvent with static + dynamic content
    2. events_to_messages() converts events to LLM messages
    3. LLM._apply_prompt_caching() applies cache markers correctly

    The static system prompt should be marked for caching (cache_prompt=True),
    while the dynamic context should NOT be marked (cache_prompt=False) to
    enable cross-conversation cache sharing.
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

    # Create agent with dynamic context
    context = AgentContext(
        system_message_suffix="Working directory: /workspace\nUser: test@example.com"
    )
    agent = Agent(llm=llm, agent_context=context)

    # Create conversation state using the factory method
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

    # Initialize state - this should create SystemPromptEvent
    agent.init_state(state, on_event=on_event)

    # Verify SystemPromptEvent was created with dynamic context
    assert len(collected_events) == 1
    system_event = collected_events[0]
    assert isinstance(system_event, SystemPromptEvent)
    assert system_event.dynamic_context is not None
    assert "Working directory" in system_event.dynamic_context.text

    # Add a user message to complete the conversation setup
    user_message = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text="Hello")],
        ),
    )
    state.events.append(user_message)

    # Convert events to messages (simulating what happens before LLM call)
    llm_convertible_events = [
        e for e in state.events if isinstance(e, LLMConvertibleEvent)
    ]
    messages = LLMConvertibleEvent.events_to_messages(llm_convertible_events)

    # Verify message structure before caching
    assert len(messages) == 2
    assert messages[0].role == "system"
    assert len(messages[0].content) == 2  # static + dynamic blocks
    assert messages[0].content[0].cache_prompt is False  # Not yet marked
    assert messages[0].content[1].cache_prompt is False  # Not yet marked

    # Apply prompt caching (simulating what LLM.format_messages_for_llm does)
    llm._apply_prompt_caching(messages)

    # Verify cache markers are correctly applied
    assert messages[0].content[0].cache_prompt is True, (
        "Static system prompt (index 0) should be marked for caching"
    )
    assert messages[0].content[1].cache_prompt is False, (
        "Dynamic context (index 1) should NOT be marked for caching"
    )
    assert messages[1].content[-1].cache_prompt is True, (
        "Last user message content should be marked for caching"
    )


def test_end_to_end_caching_flow_without_dynamic_context(tmp_path):
    """Integration test: init_state() → events_to_messages() → caching without context.

    When no AgentContext is provided, the system message should have only one
    content block (the static prompt), and it should be marked for caching.
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

    # Create agent without dynamic context
    agent = Agent(llm=llm, agent_context=None)

    # Create conversation state using the factory method
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

    # Initialize state
    agent.init_state(state, on_event=on_event)

    # Verify SystemPromptEvent was created without dynamic context
    assert len(collected_events) == 1
    system_event = collected_events[0]
    assert isinstance(system_event, SystemPromptEvent)
    assert system_event.dynamic_context is None

    # Add a user message
    user_message = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text="Hello")],
        ),
    )
    state.events.append(user_message)

    # Convert events to messages
    llm_convertible_events = [
        e for e in state.events if isinstance(e, LLMConvertibleEvent)
    ]
    messages = LLMConvertibleEvent.events_to_messages(llm_convertible_events)

    # Verify message structure
    assert len(messages) == 2
    assert messages[0].role == "system"
    assert len(messages[0].content) == 1  # Only static block

    # Apply prompt caching
    llm._apply_prompt_caching(messages)

    # Verify cache marker is applied to the single block
    assert messages[0].content[0].cache_prompt is True, (
        "Single static system prompt should be marked for caching"
    )


def test_cross_conversation_cache_sharing_simulation(tmp_path):
    """Simulate two conversations and verify cache sharing potential.

    This test simulates two different conversations with different dynamic
    contexts but the same static system prompt. It verifies that:
    1. Both conversations have identical static system prompts
    2. Cache markers are applied only to the static portion
    3. The dynamic portions are different but not cached

    This demonstrates that cross-conversation cache sharing is possible.
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

    # Simulate two conversations with different contexts
    contexts = [
        AgentContext(system_message_suffix="User: alice\nRepo: project-a"),
        AgentContext(system_message_suffix="User: bob\nRepo: project-b"),
    ]

    static_prompts = []
    dynamic_contexts = []

    for i, ctx in enumerate(contexts):
        agent = Agent(llm=llm, agent_context=ctx)

        # Create conversation state using the factory method
        conv_dir = tmp_path / f"conv_{i}"
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

        # Add user message
        user_message = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Hi")],
            ),
        )
        state.events.append(user_message)

        # Convert to messages and apply caching
        llm_convertible_events = [
            e for e in state.events if isinstance(e, LLMConvertibleEvent)
        ]
        messages = LLMConvertibleEvent.events_to_messages(llm_convertible_events)
        llm._apply_prompt_caching(messages)

        # Collect static and dynamic content
        static_block = messages[0].content[0]
        dynamic_block = messages[0].content[1]
        assert isinstance(static_block, TextContent)
        assert isinstance(dynamic_block, TextContent)
        static_prompts.append(static_block.text)
        dynamic_contexts.append(dynamic_block.text)

        # Verify cache markers
        assert static_block.cache_prompt is True
        assert dynamic_block.cache_prompt is False

    # Verify static prompts are identical (enables cache sharing)
    assert static_prompts[0] == static_prompts[1], (
        "Static system prompts must be identical for cross-conversation cache sharing"
    )

    # Verify dynamic contexts are different (as expected)
    assert dynamic_contexts[0] != dynamic_contexts[1], (
        "Dynamic contexts should be different between conversations"
    )
    assert "alice" in dynamic_contexts[0]
    assert "bob" in dynamic_contexts[1]
