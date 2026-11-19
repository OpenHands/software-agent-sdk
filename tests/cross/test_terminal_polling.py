"""Offline reproduction of native running polls; no model or Sail calls."""

import json
import platform
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.stuck_detector import StuckDetector
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.tool import Action, Observation
from openhands.tools.terminal.definition import TerminalAction, TerminalObservation
from openhands.tools.terminal.impl import TerminalExecutor


class PollAction(Action):
    command: str = "poll"


class PollObservation(Observation):
    pass


pytestmark = pytest.mark.skipif(platform.system() == "Windows", reason="Unix PTY")


@pytest.fixture
def terminal(tmp_path: Path) -> Iterator[TerminalExecutor]:
    executor = TerminalExecutor(working_dir=str(tmp_path), terminal_type="subprocess")
    try:
        yield executor
    finally:
        executor.close()


def wire_roundtrip(observation: Observation) -> Observation:
    frame = json.loads(
        json.dumps(
            {
                "kind": "terminal_observation",
                "observation": observation.model_dump(mode="json"),
            }
        )
    )
    return Observation.model_validate(frame["observation"])


def append_pair(
    session: LocalConversation, action: Action, observation: Observation
) -> None:
    call_id = str(uuid.uuid4())
    event = ActionEvent(
        thought=[],
        action=action,
        tool_name="terminal",
        tool_call_id=call_id,
        tool_call=MessageToolCall(
            id=call_id,
            name="terminal",
            arguments=action.model_dump_json(),
            origin="completion",
        ),
        llm_response_id=str(uuid.uuid4()),
    )
    session.state.append_event(event)
    session.state.append_event(
        ObservationEvent(
            observation=wire_roundtrip(observation),
            tool_name="terminal",
            tool_call_id=call_id,
            action_id=event.id,
        )
    )


def test_running_metadata_survives_wire_and_command_can_finish(
    terminal: TerminalExecutor,
):
    running = terminal(TerminalAction(command="sleep 1; printf done", timeout=0.01))
    restored = wire_roundtrip(running)
    assert isinstance(restored, TerminalObservation)
    assert restored == running
    assert restored.exit_code == restored.metadata.exit_code == -1
    assert not restored.is_error
    assert restored.is_in_progress
    assert terminal.session.is_running()
    finished = wire_roundtrip(
        terminal(TerminalAction(command="", is_input=True, timeout=5))
    )
    assert isinstance(finished, TerminalObservation)
    assert finished.exit_code == finished.metadata.exit_code == 0
    assert "done" in finished.text
    assert not terminal.session.is_running()
    assert not finished.is_in_progress


@pytest.mark.parametrize(
    "running", [False, True], ids=["completed-loop", "running-polls"]
)
def test_native_terminal_poll_stuck_detection(
    tmp_path: Path, terminal: TerminalExecutor, running: bool
):
    session = Conversation(
        agent=Agent(llm=LLM(model="openai/offline-terminal-test"), tools=[]),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "conversation"),
        visualizer=None,
    )
    try:
        session.send_message("Run the terminal fixture without model inference.")
        detector = StuckDetector(session.state)
        if running:
            terminal(TerminalAction(command="sleep 5; printf done", timeout=0.01))
            action = TerminalAction(command="", is_input=True, timeout=0.01)
        else:
            action = TerminalAction(command="true", timeout=5)
        for _ in range(detector.action_observation_threshold):
            observation = terminal(action)
            assert terminal.session.is_running() == running
            assert observation.exit_code == (-1 if running else 0)
            append_pair(session, action, observation)
        assert detector.is_stuck() == (not running)
        if running:
            finished = terminal(TerminalAction(command="", is_input=True, timeout=10))
            assert finished.exit_code == 0 and "done" in finished.text
            assert not finished.is_in_progress
            for _ in range(detector.action_observation_threshold):
                append_pair(
                    session,
                    TerminalAction(command="true"),
                    terminal(TerminalAction(command="true", timeout=5)),
                )
            assert detector.is_stuck()
    finally:
        session.close()


@pytest.mark.parametrize("is_in_progress", [False, True])
@pytest.mark.parametrize("is_error", [False, True])
@pytest.mark.parametrize("alternating", [False, True])
def test_generic_progress_observation_loops(
    tmp_path: Path, is_in_progress: bool, is_error: bool, alternating: bool
):
    session = Conversation(
        agent=Agent(llm=LLM(model="openai/offline-terminal-test"), tools=[]),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "conversation"),
        visualizer=None,
    )
    try:
        detector = StuckDetector(session.state)
        for i in range(detector.alternating_pattern_threshold):
            text = str(i % 2) if alternating else "unchanged"
            append_pair(
                session,
                PollAction(command=text),
                PollObservation.from_text(
                    text, is_error=is_error, is_in_progress=is_in_progress
                ),
            )
        assert detector.is_stuck() == (is_error or not is_in_progress)
    finally:
        session.close()


def test_rejected_commands_while_running_remain_detectable(
    tmp_path: Path, terminal: TerminalExecutor
):
    session = Conversation(
        agent=Agent(llm=LLM(model="openai/offline-terminal-test"), tools=[]),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "conversation"),
        visualizer=None,
    )
    try:
        terminal(TerminalAction(command="sleep 5", timeout=0.01))
        detector = StuckDetector(session.state)
        action = TerminalAction(command="echo rejected", timeout=0.01)
        for _ in range(detector.action_observation_threshold):
            observation = terminal(action)
            assert terminal.session.is_running()
            assert observation.is_error and not observation.is_in_progress
            append_pair(session, action, observation)
        assert detector.is_stuck()
    finally:
        session.close()
