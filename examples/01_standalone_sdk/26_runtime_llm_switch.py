"""Demonstrate switching LLM profiles at runtime and persisting the result."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, LLMRegistry, Message, TextContent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
# 1. Configure the API key for the provider you want to use
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

# 2. Disable inline conversations so profile references are stored instead
os.environ.setdefault("OPENHANDS_INLINE_CONVERSATIONS", "false")

# 3. Profiles live under ~/.openhands/llm-profiles by default. We create two
#    variants that share the same usage_id so they can be swapped at runtime.
registry = LLMRegistry()
usage_id = "support-agent"

base_profile_id = "support-mini"
alt_profile_id = "support-pro"

base_llm = LLM(
    usage_id=usage_id,
    model="litellm_proxy/anthropic/claude-sonnet-4-5-20250929",
    base_url="https://llm-proxy.eval.all-hands.dev",
    api_key=SecretStr(api_key),
    temperature=0.0,
)
alt_llm = base_llm.model_copy(
    update={
        "model": "litellm_proxy/anthropic/claude-3-5-sonnet-20240620",
        "temperature": 0.4,
    }
)

registry.save_profile(base_profile_id, base_llm)
registry.save_profile(alt_profile_id, alt_llm)

logger.info("Saved profiles %s and %s", base_profile_id, alt_profile_id)

# ---------------------------------------------------------------------------
# Start a conversation with the base profile and persist it to disk
# ---------------------------------------------------------------------------
conversation_id = uuid.uuid4()
persistence_dir = Path("./.conversations_switch_demo").resolve()
workspace_dir = Path.cwd()

agent = Agent(llm=registry.load_profile(base_profile_id), tools=[])
conversation = Conversation(
    agent=agent,
    workspace=str(workspace_dir),
    persistence_dir=str(persistence_dir),
    conversation_id=conversation_id,
    visualizer=None,
)

conversation.send_message(
    Message(
        role="user",
        content=[TextContent(text="What model are you using? Keep it short.")],
    )
)
conversation.run()

print("First run finished with profile:", conversation.agent.llm.profile_id)

# ---------------------------------------------------------------------------
# Switch to the alternate profile while the conversation is idle
# ---------------------------------------------------------------------------
conversation.switch_llm(alt_profile_id)
print("Switched runtime profile to:", conversation.agent.llm.profile_id)

conversation.send_message(
    Message(
        role="user", content=[TextContent(text="Now say hello using the new profile.")]
    )
)
conversation.run()

print("Second run finished with profile:", conversation.agent.llm.profile_id)

# Verify the persistence artefacts mention the new profile
base_state_path = Path(conversation.state.persistence_dir or ".") / "base_state.json"
print("base_state.json saved to:", base_state_path)
state_payload = json.loads(base_state_path.read_text())
print("Persisted profile entry:", state_payload["agent"]["llm"])

# ---------------------------------------------------------------------------
# Delete the in-memory conversation and reload from disk
# ---------------------------------------------------------------------------
print("\nReloading conversation from disk...")
del conversation

reloaded_agent = Agent(llm=registry.load_profile(alt_profile_id), tools=[])
reloaded = Conversation(
    agent=reloaded_agent,
    workspace=str(workspace_dir),
    persistence_dir=str(persistence_dir),
    conversation_id=conversation_id,
    visualizer=None,
)

print("Reloaded conversation is using profile:", reloaded.state.agent.llm.profile_id)
print("Active LLM model:", reloaded.state.agent.llm.model)

reloaded.send_message(
    Message(role="user", content=[TextContent(text="Confirm you survived a reload.")])
)
reloaded.run()

print("Reloaded run finished with profile:", reloaded.state.agent.llm.profile_id)

# ---------------------------------------------------------------------------
# Part 2: Inline persistence rejects runtime switching
# ---------------------------------------------------------------------------
# When OPENHANDS_INLINE_CONVERSATIONS is true the conversation persists full
# LLM payloads instead of profile references. Switching profiles would break
# the diff reconciliation step, so the SDK deliberately rejects it with a
# RuntimeError. We demonstrate that behaviour below.
os.environ["OPENHANDS_INLINE_CONVERSATIONS"] = "true"

inline_persistence_dir = Path("./.conversations_switch_demo_inline").resolve()
inline_agent = Agent(llm=registry.load_profile(base_profile_id), tools=[])
inline_conversation = Conversation(
    agent=inline_agent,
    workspace=str(workspace_dir),
    persistence_dir=str(inline_persistence_dir),
    conversation_id=uuid.uuid4(),
    visualizer=None,
)

try:
    inline_conversation.switch_llm(alt_profile_id)
except RuntimeError as exc:
    print("Inline mode switch attempt rejected as expected:", exc)
else:
    raise AssertionError("Inline mode should have rejected the LLM switch")
