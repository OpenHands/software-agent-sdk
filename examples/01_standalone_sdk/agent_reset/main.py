"""Model-requested context reset and retrieval from the original event history.

Run with LLM_MODEL, LLM_API_KEY and optionally LLM_BASE_URL, or pass --offline
for a deterministic demonstration using scripted TestLLM responses.
"""

import argparse
import json
import os
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Conversation,
    Message,
    OpenHandsAgentSettings,
    TextContent,
)
from openhands.sdk.event import Condensation, ObservationEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.settings import AgentResetCondenserSettings
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorObservation, FileEditorTool


def offline_llm(note_path: Path) -> TestLLM:
    responses: list[Message | Exception] = [
        Message(
            role="assistant",
            tool_calls=[
                MessageToolCall(
                    id="save-note",
                    name="str_replace_editor",
                    arguments=json.dumps(
                        {
                            "command": "create",
                            "path": str(note_path),
                            "file_text": (
                                "Approval state: release-ready.\n"
                                "The launch record is in conversation history.\n"
                            ),
                        }
                    ),
                    origin="completion",
                )
            ],
        ),
        Message(
            role="assistant", content=[TextContent(text="Launch record received.")]
        ),
    ]
    for index, (name, arguments) in enumerate(
        [
            (
                "new_context",
                {
                    "handoff": (
                        "Phase 2: verify the launch record in history. "
                        f"Approval note: {note_path}."
                    )
                },
            ),
            (
                "str_replace_editor",
                {"command": "view", "path": str(note_path)},
            ),
            ("conversation_history", {"command": "search", "query": "launch record"}),
        ]
    ):
        responses.append(
            Message(
                role="assistant",
                tool_calls=[
                    MessageToolCall(
                        id=f"call-{index}",
                        name=name,
                        arguments=json.dumps(arguments),
                        origin="completion",
                    )
                ],
            )
        )
    responses.append(
        Message(
            role="assistant",
            content=[
                TextContent(text="Release-ready: launch code ZX-4916, region east.")
            ],
        )
    )
    return TestLLM.from_messages(responses)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="openhands-agent-reset-") as directory:
        note_path = Path(directory) / "approval.md"
        if args.offline:
            print("Offline demonstration: model responses are scripted.")
            llm = offline_llm(note_path)
        else:
            llm = LLM(
                model=os.environ["LLM_MODEL"],
                api_key=SecretStr(os.environ["LLM_API_KEY"]),
                base_url=os.getenv("LLM_BASE_URL"),
            )
        settings = OpenHandsAgentSettings(
            llm=llm,
            tools=[Tool(name=FileEditorTool.name)],
            condenser=AgentResetCondenserSettings(),
        )
        with closing(
            Conversation(
                agent=settings.create_agent(),
                workspace=directory,
                persistence_dir=Path(directory) / "history",
                max_iteration_per_run=8,
            )
        ) as conversation:
            conversation.send_message(
                "Phase 1. Launch record: code ZX-4916, region east. "
                "Approval state: release-ready. Save the approval state and a "
                f"pointer to the historical launch record in {note_path}; omit "
                "the code and region from the file. Then confirm phase completion."
            )
            with conversation.state:
                source_id = conversation.state.active_branch()[-1].id
            conversation.run()
            conversation.send_message(
                "Begin phase 2 in a fresh context. Keep only source references in "
                "your handoff, without the record values or approval state. Verify "
                "the approval state, launch code, and region for release, then "
                "report all three with evidence from the appropriate sources."
            )
            conversation.run()
            with conversation.state:
                events = conversation.state.active_branch()
                assert source_id not in {
                    event.id for event in conversation.state.view.events
                }
            resets = sum(isinstance(event, Condensation) for event in events)
            assert resets >= 1
            assert "release-ready" in note_path.read_text()
            reset_index = next(
                index
                for index, event in enumerate(events)
                if isinstance(event, Condensation)
            )
            assert any(
                isinstance(event, ObservationEvent)
                and isinstance(event.observation, FileEditorObservation)
                and event.observation.command == "view"
                and not event.observation.is_error
                and "release-ready" in event.observation.text
                for event in events[reset_index + 1 :]
            )
            assert any(
                isinstance(event, ObservationEvent)
                and event.tool_name == "conversation_history"
                and not event.observation.is_error
                and "ZX-4916" in event.observation.text
                and "east" in event.observation.text
                for event in events
            )
            print(f"Context resets: {resets}; approval file and history retrieved.")
            cost = (
                conversation.conversation_stats.get_combined_metrics().accumulated_cost
            )
            print(f"EXAMPLE_COST: {cost}")


if __name__ == "__main__":
    main()
