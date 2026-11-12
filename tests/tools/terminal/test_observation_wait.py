"""Real PTY regressions; no model or external runtime is involved."""

import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from openhands.sdk.tool import Tool, resolve_tool
from openhands.tools.terminal import TerminalTool
from openhands.tools.terminal.definition import TerminalAction, TerminalObservation
from openhands.tools.terminal.impl import TerminalExecutor
from tests.tools.terminal.test_terminal_tool import _create_test_conv_state


if TYPE_CHECKING or platform.system() != "Windows":
    from openhands.tools.terminal.terminal.subprocess_terminal import SubprocessTerminal


pytestmark = [
    pytest.mark.skipif(platform.system() == "Windows", reason="Unix PTY"),
    pytest.mark.timeout(15),
]


@pytest.fixture(autouse=True)
def isolated_shell_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("PROMPT_COMMAND", "PS1", "BASH_ENV", "ENV", "SHELLOPTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OH_RUNTIME_IDLE_TIMEOUT_SECONDS", raising=False)


@pytest.fixture
def executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OH_RUNTIME_IDLE_TIMEOUT_SECONDS", raising=False)
    instance = TerminalExecutor(str(tmp_path), terminal_type="subprocess")
    assert instance(TerminalAction(command="echo ready", timeout=2)).exit_code == 0
    yield instance
    instance.close()


@pytest.mark.parametrize("command", ["exit", "false || exit 1", "false || exit", "C-d"])
def test_shell_death_returns_and_cleans_up(executor: TerminalExecutor, command: str):
    terminal = executor.session.terminal
    assert isinstance(terminal, SubprocessTerminal)
    process = terminal.process
    start = time.monotonic()
    obs = executor(TerminalAction(command=command, timeout=900000))
    assert time.monotonic() - start < 3
    assert obs.is_error and obs.exit_code == -1
    assert "reset" in obs.text.lower()
    assert terminal.closed
    again = executor(TerminalAction(command="echo should-not-run", timeout=900000))
    assert again.is_error
    executor.close()
    assert process is not None and process.poll() is not None
    assert terminal._pty_master_fd is None
    assert terminal.reader_thread is not None and not terminal.reader_thread.is_alive()


@pytest.mark.parametrize("command", ["printf 'unfinished", "cat <<'END'\nunfinished"])
def test_incomplete_input_soft_returns(executor: TerminalExecutor, command: str):
    executor.session.max_observation_wait_seconds = 0.2
    start = time.monotonic()
    obs = executor(TerminalAction(command=command, timeout=900000))
    assert time.monotonic() - start < 1.5
    assert obs.exit_code == -1 and not obs.is_error
    assert not executor.session.terminal.closed
    obs = executor(TerminalAction(command="C-c", is_input=True, timeout=1))
    assert not obs.is_error


def test_computation_survives_initial_and_poll_waits(executor: TerminalExecutor):
    executor.session.max_observation_wait_seconds = 0.2
    command = (
        "python -c 'import time; t=time.monotonic(); "
        'exec("while time.monotonic()-t<1.5: pass"); print(123456)' + "'"
    )
    start = time.monotonic()
    obs = executor(TerminalAction(command=command, timeout=900000))
    assert time.monotonic() - start < 1
    assert obs.exit_code == -1
    start = time.monotonic()
    obs = executor(TerminalAction(command="", is_input=True, timeout=900000))
    assert time.monotonic() - start < 1
    assert obs.exit_code == -1
    for _ in range(20):
        obs = executor(TerminalAction(command="", is_input=True, timeout=900000))
        if obs.exit_code != -1:
            break
    assert obs.exit_code == 0 and "123456" in obs.text


def test_close_cancels_wait(executor: TerminalExecutor):
    started = threading.Event()

    def execute():
        started.set()
        return executor(TerminalAction(command="sleep 100", timeout=900000))

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(execute)
        assert started.wait(1)
        time.sleep(0.2)
        executor.close()
        assert result.result(timeout=4).is_error


@pytest.mark.parametrize("terminal_type", ["subprocess", "tmux"])
def test_tool_registration_reset_and_polling(tmp_path: Path, terminal_type: str):
    state = _create_test_conv_state(str(tmp_path))
    spec = Tool(
        name=TerminalTool.name,
        params={
            "terminal_type": terminal_type,
            "max_observation_wait_seconds": 0.2,
        },
    )
    # Resolve a round-tripped tool spec, as native learners register it.
    tool = cast(
        TerminalTool,
        resolve_tool(Tool.model_validate_json(spec.model_dump_json()), state)[0],
    )
    executor = cast(TerminalExecutor, tool.executor)
    try:
        for reset in (False, True):
            if reset:
                tool(TerminalAction(command="", reset=True))
            assert executor._max_observation_wait_seconds == 0.2
            if not executor.is_pooled:
                assert executor.session.max_observation_wait_seconds == 0.2
            tool(TerminalAction(command="echo ready", timeout=2))
            start = time.monotonic()
            obs = tool(
                TerminalAction(command="sleep 0.8; echo finished", timeout=900000)
            )
            assert isinstance(obs, TerminalObservation)
            assert time.monotonic() - start < 1
            assert obs.exit_code == -1 and not obs.is_error
            for _ in range(10):
                start = time.monotonic()
                obs = tool(TerminalAction(command="", is_input=True, timeout=900000))
                assert isinstance(obs, TerminalObservation)
                assert time.monotonic() - start < 1
                if obs.exit_code != -1:
                    break
            assert obs.exit_code == 0 and "finished" in obs.text
    finally:
        executor.close()


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_observation_wait_rejected_before_start(tmp_path: Path, value: float):
    with pytest.raises(ValueError, match="finite and positive"):
        TerminalExecutor(
            str(tmp_path),
            terminal_type="subprocess",
            max_observation_wait_seconds=value,
        )


def test_default_wait_and_shorter_action_timeout(executor: TerminalExecutor):
    assert executor.session.max_observation_wait_seconds is None
    start = time.monotonic()
    obs = executor(TerminalAction(command="sleep 0.4; echo done", timeout=2))
    assert time.monotonic() - start >= 0.4
    assert obs.exit_code == 0
    executor.session.max_observation_wait_seconds = 2
    start = time.monotonic()
    obs = executor(TerminalAction(command="sleep 100", timeout=0.1))
    assert time.monotonic() - start < 1
    assert obs.exit_code == -1
    obs = executor(TerminalAction(command="C-c", is_input=True, timeout=2))
    assert obs.exit_code != -1


def test_eof_preserves_final_output(executor: TerminalExecutor):
    obs = executor(
        TerminalAction(
            command="printf '%s%s' 'final-' 'output'; exit 7", timeout=900000
        )
    )
    assert obs.is_error and "final-output" in obs.text


def test_continuous_output_is_bounded(executor: TerminalExecutor):
    executor.session.max_observation_wait_seconds = 0.2
    start = time.monotonic()
    obs = executor(
        TerminalAction(command="for i in {1..20}; do echo tick; sleep 0.05; done")
    )
    assert time.monotonic() - start < 1
    assert obs.exit_code == -1 and "tick" in obs.text
    obs = executor(TerminalAction(command="C-c", is_input=True, timeout=2))
    assert obs.exit_code != -1


def test_shell_death_with_inherited_pty_returns(executor: TerminalExecutor):
    start = time.monotonic()
    obs = executor(
        TerminalAction(
            command="(while true; do echo tick; done) & exit 1", timeout=900000
        )
    )
    assert time.monotonic() - start < 3
    assert obs.is_error


def test_reset_recovers_dead_shell(executor: TerminalExecutor):
    assert executor(TerminalAction(command="exit", timeout=900000)).is_error
    obs = executor(TerminalAction(command="", reset=True))
    assert obs.exit_code == 0
    executor(TerminalAction(command="echo ready", timeout=2))
    obs = executor(TerminalAction(command="echo recovered", timeout=2))
    assert obs.exit_code == 0 and "recovered" in obs.text


def test_clear_without_prompt_does_not_send_input(executor: TerminalExecutor):
    terminal = executor.session.terminal
    assert isinstance(terminal, SubprocessTerminal)
    obs = executor(
        TerminalAction(
            command="printf 'waiting'; read answer; printf '<%s>' \"$answer\"",
            timeout=0.1,
        )
    )
    assert obs.exit_code == -1
    with terminal.output_lock:
        terminal.output_buffer.clear()
    terminal.clear_screen()
    obs = executor(TerminalAction(command="", is_input=True, timeout=0.2))
    assert obs.exit_code == -1, "clear_screen sent Enter to the waiting read"


def test_slow_subprocess_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".bashrc").write_text('sleep 1.5; touch "$HOME/rc-finished"\n')
    # An inherited OpenHands prompt can render before our setup is consumed.
    from openhands.tools.terminal.metadata import CmdOutputMetadata

    monkeypatch.setenv(
        "PROMPT_COMMAND", f'export PS1="{CmdOutputMetadata.to_ps1_prompt()}"'
    )
    executor = TerminalExecutor(str(tmp_path), terminal_type="subprocess")
    try:
        assert (tmp_path / "rc-finished").exists(), "initialize returned during bashrc"
        obs = executor(TerminalAction(command="sleep 2; echo actual-finish", timeout=3))
        assert obs.exit_code == 0 and "actual-finish" in obs.text
    finally:
        executor.close()
