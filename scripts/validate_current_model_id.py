"""Validate that ACPAgent.current_model_id is populated end-to-end.

Spins up an ACP subprocess for each scenario, runs init_state (which calls
new_session under the hood), and prints the resolved current_model_id.
Does not run a full conversation turn — we only want to verify the
session-response extraction path.

Usage:
    uv run python scripts/validate_current_model_id.py
"""

from __future__ import annotations

import os
import sys
import traceback
import uuid

from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.workspace.local import LocalWorkspace


def _make_state(agent: ACPAgent) -> ConversationState:
    workspace = LocalWorkspace(working_dir=os.getcwd())
    return ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=workspace,
    )


def run_scenario(label: str, agent: ACPAgent) -> None:
    print(f"\n=== {label} ===")
    print(f"  acp_command       = {agent.acp_command}")
    print(f"  acp_model         = {agent.acp_model!r}")
    state = _make_state(agent)
    try:
        agent.init_state(state, on_event=lambda _evt: None)
        print(f"  agent_name        = {agent.agent_name!r}")
        print(f"  agent_version     = {agent.agent_version!r}")
        print(f"  current_model_id   = {agent.current_model_id!r}")
        print(f"  current_model_name = {agent.current_model_name!r}")
        if agent.current_model_id:
            same = agent.current_model_name == agent.current_model_id
            print(
                "  ✓ current_model_id populated"
                + ("" if same else " (and name resolves to a different value)")
            )
        else:
            print(
                "  ✗ current_model_id is None — server didn't report it "
                "(may be expected if the agent predates models.currentModelId)"
            )
    except Exception:
        print("  ! init_state raised")
        traceback.print_exc()
    finally:
        agent.close()


def main() -> int:
    scenarios: list[tuple[str, ACPAgent]] = [
        (
            "Claude Code (no override → server-reported)",
            ACPAgent(
                acp_command=["npx", "-y", "@agentclientprotocol/claude-agent-acp"]
            ),
        ),
        (
            "Claude Code (acp_model='claude-opus-4-1' override)",
            ACPAgent(
                acp_command=["npx", "-y", "@agentclientprotocol/claude-agent-acp"],
                acp_model="claude-opus-4-1",
            ),
        ),
        (
            "Codex (no override → server-reported)",
            ACPAgent(acp_command=["npx", "-y", "@zed-industries/codex-acp"]),
        ),
        (
            "Codex (acp_model='gpt-5' override)",
            ACPAgent(
                acp_command=["npx", "-y", "@zed-industries/codex-acp"],
                acp_model="gpt-5",
            ),
        ),
    ]
    for label, agent in scenarios:
        run_scenario(label, agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
