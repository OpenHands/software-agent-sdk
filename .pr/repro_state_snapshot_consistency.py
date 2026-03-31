from __future__ import annotations

import argparse
import logging
import threading
import time
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.workspace import LocalWorkspace


warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module=r"litellm\.llms\.custom_httpx\.async_client_cleanup",
)
logging.getLogger("openhands.sdk.conversation.state").setLevel(logging.WARNING)


@dataclass(slots=True)
class SnapshotCheck:
    mode: str
    samples: int
    mismatches: int
    examples: list[tuple[str, int]]


class ConversationStub:
    def __init__(self, state: ConversationState) -> None:
        self._state = state


class EpochMutator:
    def __init__(self, state: ConversationState, write_gap_s: float) -> None:
        self.state = state
        self.write_gap_s = write_gap_s
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        epoch = 0
        while not self.stop_event.is_set():
            with self.state:
                self.state.tags = {"epoch": str(epoch)}
                time.sleep(self.write_gap_s)
                self.state.agent_state = {"epoch": epoch}
            epoch += 1
            time.sleep(0.0001)


def build_service() -> tuple[EventService, ConversationState]:
    agent = Agent(llm=LLM(model="gpt-4o", usage_id="consistency-repro"), tools=[])
    workspace = LocalWorkspace(working_dir="/tmp/repro-consistency-workspace")
    stored = StoredConversation(id=uuid.uuid4(), agent=agent, workspace=workspace)
    state = ConversationState.create(
        id=stored.id,
        agent=agent,
        workspace=workspace,
        persistence_dir=None,
    )
    state.tags = {"epoch": "-1"}
    state.agent_state = {"epoch": -1}

    service = EventService(
        stored=stored,
        conversations_dir=Path("/tmp/repro-consistency-conversations"),
    )
    service._conversation = ConversationStub(state)
    return service, state


def take_snapshot(mode: str, service: EventService, state: ConversationState) -> dict:
    if mode == "unsafe":
        event = ConversationStateUpdateEvent.from_conversation_state(state)
    else:
        event = service._create_state_update_event_sync()
    return event.value


def run_mode(mode: str, samples: int, write_gap_s: float) -> SnapshotCheck:
    service, state = build_service()
    mutator = EpochMutator(state, write_gap_s=write_gap_s)
    mutator.start()
    mismatches = 0
    examples: list[tuple[str, int]] = []
    try:
        for _ in range(samples):
            snapshot = take_snapshot(mode, service, state)
            tag_epoch = snapshot["tags"]["epoch"]
            agent_epoch = snapshot["agent_state"]["epoch"]
            if tag_epoch != str(agent_epoch):
                mismatches += 1
                if len(examples) < 5:
                    examples.append((tag_epoch, agent_epoch))
    finally:
        mutator.stop()

    return SnapshotCheck(
        mode=mode,
        samples=samples,
        mismatches=mismatches,
        examples=examples,
    )


def validate(result: SnapshotCheck) -> None:
    if result.mode == "unsafe":
        assert result.mismatches > 0, result
        return
    assert result.mismatches == 0, result


def print_result(result: SnapshotCheck) -> None:
    print(
        f"[{result.mode}] {result.mismatches}/{result.samples} snapshots violated "
        "the tags/agent_state epoch invariant."
    )
    for tag_epoch, agent_epoch in result.examples:
        print(
            f"[{result.mode}] example mismatch: tags.epoch={tag_epoch!r}, "
            f"agent_state.epoch={agent_epoch!r}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["unsafe", "current", "both"],
        default="both",
        help="Which implementation to run.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=200,
        help="How many snapshots to capture per mode.",
    )
    parser.add_argument(
        "--write-gap-ms",
        type=float,
        default=2.0,
        help="Delay between correlated field updates while the writer holds the lock.",
    )
    args = parser.parse_args()

    modes = [args.mode] if args.mode != "both" else ["unsafe", "current"]
    write_gap_s = args.write_gap_ms / 1000.0
    results = [
        run_mode(mode, samples=args.samples, write_gap_s=write_gap_s) for mode in modes
    ]
    for result in results:
        print_result(result)
        validate(result)

    if args.mode == "both":
        print(
            "Repro succeeded: unlocked snapshots can observe inconsistent full-state "
            "epochs, while the current PR preserves the locked snapshot guarantee."
        )


if __name__ == "__main__":
    main()
