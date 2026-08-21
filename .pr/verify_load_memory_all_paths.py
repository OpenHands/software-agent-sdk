"""Manual, less-mocked verification for #4542.

Proves, against real file I/O instead of pytest mocks:
  Part A - load_memory() actually reads a real MEMORY.md from disk.
  Part B - a real, on-disk load_memory=True preference reaches the launched
           agent for both previously-broken shapes, `agent` and
           `agent_settings`. `agent_profile_id` is not re-checked here -
           that path was already fixed by #4223 and is covered by the
           existing pytest suite.

Run with: uv run python .pr/verify_load_memory_all_paths.py
Requires OH_PERSISTENCE_DIR set first - without it this would write to your
real ~/.openhands directory.
"""

import asyncio
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_settings_store,
    reset_stores,
)
from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.context.memory import load_memory
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.settings.model import OpenHandsAgentSettings
from openhands.sdk.workspace import LocalWorkspace


def make_agent() -> Agent:
    return Agent(llm=LLM(model="gpt-4o", usage_id="llm"), tools=[])


async def launch_and_get_agent(tmp_path: Path, **request_kwargs):
    """Launch through the real ConversationService; stub only the expensive
    last-mile (actual agent execution / LLM call), never the settings store."""
    request = StartConversationRequest(
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        **request_kwargs,
    )
    captured = {}

    async def capture_start(_stored, **kwargs):
        agent = kwargs["agent"]
        captured["agent"] = agent
        es = AsyncMock(spec=EventService)
        es.get_state.return_value = ConversationState(
            id=uuid4(),
            agent=agent,
            workspace=request.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
        )
        es.stored = MagicMock(
            launched_agent_profile=None,
            client_tools=[],
            title=None,
            metrics=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            forked_from_conversation_id=None,
            forked_from_event_id=None,
            parent_conversation_id=None,
        )
        return es

    service = ConversationService(conversations_dir=tmp_path)
    service._event_services = {}
    with patch.object(
        service,
        "_start_event_service",
        new_callable=AsyncMock,
        side_effect=capture_start,
    ):
        await service.start_conversation(request)
    return captured["agent"]


async def main():
    persistence_dir = Path(os.environ["OH_PERSISTENCE_DIR"])
    reset_stores()  # safety net; harmless if there was nothing to reset
    get_settings_store().save(
        PersistedSettings(
            agent_settings=OpenHandsAgentSettings(
                agent_context=AgentContext(load_memory=True)
            )
        )
    )
    print(
        f"Persisted load_memory=True for real at {persistence_dir / 'settings.json'}\n"
    )

    with tempfile.TemporaryDirectory() as workspace_dir:
        workspace = Path(workspace_dir)
        memory_dir = workspace / ".openhands" / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "MEMORY.md").write_text(
            "# Verification note\n"
            "load_memory propagated correctly if you can read this.\n"
        )

        print("=== Part A: does load_memory() read the file back? ===")
        content = load_memory(workspace)
        print(content)
        assert content is not None and "propagated correctly" in content
        print("PASS\n")

        print("=== Part B: does the real settings store reach the launched agent? ===")
        for label, kwargs in [
            ("agent", {"agent": make_agent()}),
            (
                "agent_settings",
                {
                    "agent_settings": {
                        "agent_kind": "openhands",
                        "llm": {"model": "gpt-4o", "usage_id": "llm"},
                    }
                },
            ),
        ]:
            agent = await launch_and_get_agent(workspace, **kwargs)
            ok = agent.agent_context is not None and agent.agent_context.load_memory
            print(f"{label:15s} -> agent_context.load_memory = {ok}")
            assert ok, f"{label} did not inherit the persisted preference"

    print("\nAll checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
