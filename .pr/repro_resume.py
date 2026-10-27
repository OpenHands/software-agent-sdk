"""Offline, real SDK create/close/process-restart resume workflow for #5710."""

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import uuid
from contextlib import closing
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import Agent, Conversation
from openhands.sdk.agent import agent as agent_module
from openhands.sdk.agent.utils import aprepare_llm_messages, prepare_llm_messages
from openhands.sdk.event import SystemPromptEvent
from openhands.sdk.hooks.config import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.llm import LLM


def run_phase(phase: str, root: Path, conversation_id: uuid.UUID) -> None:
    llm = LLM(model="openai/gpt-4o-mini", api_key=SecretStr("offline-placeholder"))
    agent = Agent(llm=llm, tools=[])
    hooks = HookConfig(
        session_start=[
            HookMatcher(hooks=[HookDefinition(command="true") for _ in range(3)])
        ]
    )
    with closing(
        Conversation(
            agent=agent,
            workspace=root / "workspace",
            persistence_dir=root / "conversations",
            conversation_id=conversation_id,
            hook_config=hooks,
            visualizer=None,
            delete_on_close=False,
        )
    ) as conversation:
        before = len(conversation.state.events)
        conversation.send_message(f"Offline {phase}: do not call a model.")
        with conversation.state:
            events = list(conversation.state.events)
            messages = prepare_llm_messages(
                conversation.state.view, condenser=None, llm=conversation.agent.llm
            )
            async_messages = asyncio.run(
                aprepare_llm_messages(
                    conversation.state.view,
                    condenser=None,
                    llm=conversation.agent.llm,
                )
            )
        assert isinstance(messages, list)
        assert isinstance(async_messages, list)
        report = {
            "phase": phase,
            "sdk_source": agent_module.__file__,
            "conversation_id": str(conversation.id),
            "persistence_dir": conversation.state.persistence_dir,
            "events_before_send": before,
            "prefix_event_types": [type(event).__name__ for event in events[:4]],
            "system_event_indices": [
                i
                for i, event in enumerate(events)
                if isinstance(event, SystemPromptEvent)
            ],
            "sync_roles": [message.role for message in messages],
            "async_roles": [message.role for message in async_messages],
            "model_requests": 0,
        }
        print(json.dumps(report))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["create", "resume-1", "resume-2"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--conversation-id", type=uuid.UUID)
    arguments = parser.parse_args()
    if arguments.phase:
        assert arguments.root is not None and arguments.conversation_id is not None
        run_phase(arguments.phase, arguments.root, arguments.conversation_id)
        return
    root = arguments.root or Path(tempfile.mkdtemp(prefix="openhands-5710-lifecycle-"))
    conversation_id = uuid.uuid4()
    for phase in ["create", "resume-1", "resume-2"]:
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--phase",
                phase,
                "--root",
                str(root),
                "--conversation-id",
                str(conversation_id),
            ],
            check=True,
            timeout=120,
        )


if __name__ == "__main__":
    main()
