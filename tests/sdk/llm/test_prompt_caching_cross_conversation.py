"""Regression test: static system message must be constant across conversations.

This test prevents accidental introduction of dynamic content into the static
system prompt, which would break cross-conversation prompt caching.

For prompt caching to work across conversations, the system message must be
identical for all conversations regardless of per-conversation context.
"""

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent, AgentContext, LLM
from openhands.sdk.context.skills import Skill


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
