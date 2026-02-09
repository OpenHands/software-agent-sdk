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
