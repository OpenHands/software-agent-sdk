"""Terminal progress survives transport without disabling repetition detection."""

import json
import platform
import shutil
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


@pytest.fixture(params=["subprocess", "tmux"])
def terminal(
    tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[TerminalExecutor]:
    if request.param == "tmux" and shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    executor = TerminalExecutor(working_dir=str(tmp_path), terminal_type=request.param)
    try:
        yield executor
    finally:
        executor.close()


@pytest.fixture
def session(tmp_path: Path) -> Iterator[LocalConversation]:
    session = Conversation(
        agent=Agent(llm=LLM(model="openai/offline-terminal-test"), tools=[]),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "conversation"),
        visualizer=None,
    )
    try:
        yield session
    finally:
        session.close()


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
    assert terminal.is_pooled or terminal.session.is_running()
    finished = wire_roundtrip(
        terminal(TerminalAction(command="", is_input=True, timeout=5))
    )
    assert isinstance(finished, TerminalObservation)
    assert finished.exit_code == finished.metadata.exit_code == 0
    assert "done" in finished.text
    assert terminal.is_pooled or not terminal.session.is_running()
    assert not finished.is_in_progress


@pytest.mark.parametrize(
    "running", [False, True], ids=["completed-loop", "running-polls"]
)
def test_native_terminal_poll_stuck_detection(
    session: LocalConversation, terminal: TerminalExecutor, running: bool
):
    session.send_message("Run the terminal fixture without model inference.")
    detector = StuckDetector(session.state)
    if running:
        terminal(TerminalAction(command="sleep 5; printf done", timeout=0.01))
        action = TerminalAction(command="", is_input=True, timeout=0.01)
    else:
        action = TerminalAction(command="true", timeout=5)
    for _ in range(detector.action_observation_threshold):
        observation = terminal(action)
        assert terminal.is_pooled or terminal.session.is_running() == running
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


@pytest.mark.parametrize("is_in_progress", [False, True])
@pytest.mark.parametrize("is_error", [False, True])
@pytest.mark.parametrize("alternating", [False, True])
def test_generic_progress_observation_loops(
    session: LocalConversation, is_in_progress: bool, is_error: bool, alternating: bool
):
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


def test_rejected_commands_while_running_remain_detectable(
    session: LocalConversation, terminal: TerminalExecutor
):
    terminal(TerminalAction(command="sleep 5", timeout=0.01))
    detector = StuckDetector(session.state)
    action = TerminalAction(
        command="{'command': 'echo rejected'}"
        if terminal.is_pooled
        else "echo rejected",
        timeout=0.01,
    )
    for _ in range(detector.action_observation_threshold):
        observation = terminal(action)
        assert terminal.is_pooled or terminal.session.is_running()
        assert observation.is_error and not observation.is_in_progress
        append_pair(session, action, observation)
    assert detector.is_stuck()


@pytest.mark.parametrize("mode", ["no_change", "closed", "legacy"])
def test_terminal_progress_edge_cases(terminal: TerminalExecutor, mode: str):
    if mode == "legacy":
        payload = terminal(TerminalAction(command="true", timeout=5)).model_dump(
            mode="json"
        )
        payload.pop("is_in_progress")
        restored = Observation.model_validate(payload)
        assert not restored.is_in_progress
    elif mode == "closed":
        observation = terminal(TerminalAction(command="exit", timeout=1))
        assert observation.is_error and observation.exit_code == -1
        assert not observation.is_in_progress
    else:
        if terminal.is_pooled:
            pytest.skip("Single-session no-change timeout configuration")
        terminal.session.no_change_timeout_seconds = 1
        observation = terminal(TerminalAction(command="sleep 5"))
        assert "no new output" in observation.metadata.suffix
        assert observation.is_in_progress and not observation.is_error
