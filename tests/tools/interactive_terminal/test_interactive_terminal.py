"""Tests for the interactive_terminal toolset (exec_command + write_stdin).

These tests exercise the full background-monitoring pattern where an agent
starts a long-running command, receives a session_id, and polls for progress
— mirroring Codex's exec_command / write_stdin / yield_time_ms design.
"""

import platform
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.interactive_terminal import (
    ExecCommandAction,
    ExecCommandTool,
    InteractiveTerminalManager,
    InteractiveTerminalObservation,
    InteractiveTerminalToolSet,
    WriteStdinAction,
    WriteStdinTool,
)
from openhands.tools.interactive_terminal.impl import _clamp_yield


_unix_only = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Test uses bash syntax or Unix signal semantics not supported on Windows",
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def manager(tmp_dir):
    mgr = InteractiveTerminalManager(tmp_dir)
    yield mgr
    mgr.close()


def _conv_state(tmp_dir: str) -> ConversationState:
    llm = LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="t")
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=tmp_dir),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests — _clamp_yield helper
# ──────────────────────────────────────────────────────────────────────────────


def test_clamp_yield_normal_range():
    assert _clamp_yield(5_000) == 5.0


def test_clamp_yield_below_minimum():
    # Below 250 ms → clamped to 0.25 s
    assert _clamp_yield(10) == 0.25


def test_clamp_yield_above_maximum():
    # Above 30 s → clamped to 30 s
    assert _clamp_yield(999_999) == 30.0


def test_clamp_yield_empty_poll_enforces_floor():
    # Empty polls must wait at least 5 s even if caller requests less
    assert _clamp_yield(100, is_empty_poll=True) == 5.0


def test_clamp_yield_empty_poll_allows_longer():
    assert _clamp_yield(10_000, is_empty_poll=True) == 10.0


# ──────────────────────────────────────────────────────────────────────────────
# Tool registration and names
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_names_match_codex():
    """Tool names must match Codex's exec_command / write_stdin exactly."""
    assert ExecCommandTool.name == "exec_command"
    assert WriteStdinTool.name == "write_stdin"


def test_toolset_creates_two_tools(tmp_dir):
    state = _conv_state(tmp_dir)
    tools = InteractiveTerminalToolSet.create(state)
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"exec_command", "write_stdin"}


def test_toolset_accessible_via_tool_registry(tmp_dir):
    """InteractiveTerminalToolSet.name is registered and can be used with Tool()."""
    state = _conv_state(tmp_dir)
    llm = LLM(model="gpt-4o", api_key=SecretStr("k"), usage_id="t")
    agent = Agent(llm=llm, tools=[Tool(name=InteractiveTerminalToolSet.name)])
    resolved = state.workspace  # workspace init smoke-test
    assert resolved is not None
    _ = agent  # agent was constructed successfully


@_unix_only
def test_toolset_shares_manager_across_tools(tmp_dir):
    """exec_command and write_stdin from the same toolset share a manager.

    This is the key invariant introduced by get_or_create_manager: session IDs
    returned by exec_command must be valid for write_stdin from the same creation.
    """
    state = _conv_state(tmp_dir)
    tools = InteractiveTerminalToolSet.create(state)
    exec_tool = next(t for t in tools if t.name == "exec_command")
    write_tool = next(t for t in tools if t.name == "write_stdin")

    # Start a slow command — should return session_id
    exec_obs = exec_tool(ExecCommandAction(cmd="sleep 60", yield_time_ms=500))
    assert isinstance(exec_obs, InteractiveTerminalObservation)
    assert exec_obs.session_id is not None

    # write_stdin must be able to find the session using the same session_id
    poll_obs = write_tool(WriteStdinAction(session_id=exec_obs.session_id))
    assert isinstance(poll_obs, InteractiveTerminalObservation)
    # If the manager is shared, the session exists and we still get session_id back
    assert poll_obs.session_id == exec_obs.session_id

    # Cleanup
    write_tool(
        WriteStdinAction(
            session_id=exec_obs.session_id, chars="\x03", yield_time_ms=500
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
# Manager unit tests — exec_command
# ──────────────────────────────────────────────────────────────────────────────


def test_fast_command_returns_exit_code(manager):
    """Fast command finishes inline, returns exit_code (not session_id)."""
    out, wall, sid, ec, _ = manager.exec_command("echo hello", yield_time_ms=5_000)

    assert sid is None, "session_id must be absent when process finishes"
    assert ec == 0
    assert "hello" in out
    assert wall > 0


@_unix_only
def test_fast_command_nonzero_exit_code(manager):
    # Use a bash subshell so the shell session itself is not terminated.
    out, wall, sid, ec, _ = manager.exec_command("(exit 42)", yield_time_ms=5_000)

    assert sid is None
    assert ec == 42


def test_slow_command_returns_session_id(manager):
    """A command that outlasts yield_time_ms returns session_id (not exit_code)."""
    out, wall, sid, ec, _ = manager.exec_command("sleep 60", yield_time_ms=500)

    assert ec is None, "exit_code must be absent while process is running"
    assert sid is not None
    assert isinstance(sid, int)
    assert wall >= 0.5 - 0.05  # at least ~yield_time_ms

    # Interrupt so the background process doesn't linger
    manager.write_stdin(sid, chars="\x03", yield_time_ms=1_000)


def test_exec_command_workdir(tmp_dir, manager):
    """workdir parameter sets the working directory for the command."""
    import os

    sub = os.path.join(tmp_dir, "subdir")
    os.makedirs(sub, exist_ok=True)

    out, _wall, _sid, ec, _ = manager.exec_command(
        "pwd", workdir=sub, yield_time_ms=5_000
    )

    assert ec == 0
    assert sub in out


def test_exec_command_each_creates_new_session(manager):
    """Two exec_command calls must produce distinct session IDs."""
    _, _, sid1, _, _ = manager.exec_command("sleep 60", yield_time_ms=500)
    _, _, sid2, _, _ = manager.exec_command("sleep 60", yield_time_ms=500)

    assert sid1 is not None
    assert sid2 is not None
    assert sid1 != sid2

    # Cleanup
    manager.write_stdin(sid1, chars="\x03", yield_time_ms=500)
    manager.write_stdin(sid2, chars="\x03", yield_time_ms=500)


def test_max_output_tokens_truncates_output(manager):
    # Generate ~100 chars of output and limit to ~5 tokens (20 chars)
    out, _w, _sid, ec, _ = manager.exec_command(
        "python3 -c \"print('x' * 200)\"",
        yield_time_ms=5_000,
        max_output_tokens=5,  # 5 tokens * 4 chars = 20 chars max
    )
    assert ec == 0
    assert len(out) <= 20 + 5  # small slack for header chars


# ──────────────────────────────────────────────────────────────────────────────
# Manager unit tests — write_stdin
# ──────────────────────────────────────────────────────────────────────────────


def test_write_stdin_unknown_session(manager):
    """write_stdin on a non-existent session returns a meaningful error message."""
    out, wall, sid, ec, _ = manager.write_stdin(999, yield_time_ms=500)

    assert sid is None
    assert ec is None
    assert "999" in out
    assert wall == 0.0

    # Observation must not lie: header says "not found", not "completed"
    obs = InteractiveTerminalObservation.create(out, wall, sid, ec)
    assert obs.session_id is None
    assert obs.exit_code is None
    assert "not found" in obs.text.lower()
    assert "exit_code=None" not in obs.text


def test_write_stdin_polls_running_process(manager):
    """Empty-char poll returns session_id while process is still running."""
    _, _, sid, _, _ = manager.exec_command("sleep 60", yield_time_ms=500)
    assert sid is not None

    out_p, wall_p, sid_p, ec_p, _ = manager.write_stdin(
        sid, chars="", yield_time_ms=5_000
    )

    assert ec_p is None
    assert sid_p == sid
    # Empty poll must wait at least MIN_EMPTY_POLL_YIELD_SECONDS (5 s)
    assert wall_p >= 5.0 - 0.5

    # Cleanup
    manager.write_stdin(sid, chars="\x03", yield_time_ms=1_000)


@_unix_only
def test_write_stdin_interrupt_ctrl_c(manager):
    """Sending Ctrl+C (\\x03) stops the running process."""
    _, _, sid, _, _ = manager.exec_command("sleep 60", yield_time_ms=500)
    assert sid is not None

    out, wall, sid_after, ec, _ = manager.write_stdin(
        sid, chars="\x03", yield_time_ms=2_000
    )

    # After interrupt the process should be done
    assert sid_after is None
    assert ec is not None  # 130 = SIGINT on most shells


@_unix_only
def test_write_stdin_session_removed_after_completion(manager):
    """After a process finishes, its session is cleaned up from the manager."""
    _, _, sid, _, _ = manager.exec_command("sleep 60", yield_time_ms=500)
    assert sid is not None

    # Interrupt the process
    manager.write_stdin(sid, chars="\x03", yield_time_ms=2_000)

    # A subsequent poll on the same session_id must report "unknown session"
    out, _, sid2, _, _ = manager.write_stdin(sid, yield_time_ms=500)
    assert sid2 is None
    assert "completed or was never started" in out or str(sid) in out


@_unix_only
def test_write_stdin_delivers_bytes_without_enter(manager):
    """write_stdin delivers chars verbatim — no Enter keystroke appended.

    A process blocked on ``sys.stdin.read(4)`` must still be running after
    ``write_stdin(chars="abc")`` sends only 3 bytes. The terminal line
    discipline buffers them without a newline, so the process's read does
    not return. The old behaviour appended Enter, delivering ``"abc\\n"``
    (4 bytes), which made ``read(4)`` return and the process exit.
    """
    cmd = (
        'python3 -c "import sys; '
        "sys.stdout.write('GOT:' + repr(sys.stdin.read(4))); "
        'sys.stdout.flush()"'
    )
    _, _, sid, ec, _ = manager.exec_command(cmd, yield_time_ms=2_000)
    assert sid is not None, "Process should be blocked waiting for 4 stdin bytes"
    assert ec is None

    # Send 3 bytes with no Enter -> process must still be blocked (no flush).
    out1, _, sid_after, ec_after, _ = manager.write_stdin(
        sid, chars="abc", yield_time_ms=2_000
    )
    assert sid_after is not None, (
        "Process must still be running: 'abc' is only 3 bytes and no Enter was "
        "sent to flush the line discipline. If Enter were appended, read(4) "
        "would return 'abc\\n' and the process would exit."
    )
    assert ec_after is None

    # Complete the read: send 'd\\n' (4th byte + Enter to flush) -> read(4) -> 'abcd'
    out2, _, sid_final, ec_final, _ = manager.write_stdin(
        sid, chars="d\n", yield_time_ms=3_000
    )
    assert ec_final == 0, (
        f"Process should finish after 4th byte + Enter; got out={(out1 + out2)!r}"
    )
    combined = (out1 + out2).replace("\r", "")
    assert "GOT:'abcd'" in combined, f"Expected GOT:'abcd' in output, got: {combined!r}"


# ──────────────────────────────────────────────────────────────────────────────
# Integration: full background-monitoring pattern
# ──────────────────────────────────────────────────────────────────────────────


@_unix_only
def test_background_monitoring_pattern(manager):
    """Simulate the Codex agent pattern: start command, poll to completion.

    This is the core scenario the tool is designed for:
      1. exec_command with short yield → get session_id
      2. write_stdin (empty poll) in a loop until exit_code appears
    """
    # Command emits 3 lines spaced 0.3 s apart
    out, _w, sid, ec, _ = manager.exec_command(
        "for i in 1 2 3; do echo step $i; sleep 0.3; done",
        yield_time_ms=500,
    )

    assert sid is not None, "Command should still be running after 0.5 s"
    assert ec is None
    # May have partial output from the first 500 ms
    collected = out

    # Poll up to 10 times until the process finishes
    for _ in range(10):
        out_p, _w_p, sid_p, ec_p, _ = manager.write_stdin(
            sid, chars="", yield_time_ms=1_000
        )
        collected += out_p
        if ec_p is not None:
            assert ec_p == 0
            break
    else:
        pytest.fail("Process did not complete within the polling window")

    # All three steps must appear in the collected output
    for step in ("step 1", "step 2", "step 3"):
        assert step in collected, f"{step!r} missing from output: {collected!r}"


def test_multiple_concurrent_sessions(manager):
    """Multiple processes can run simultaneously and be polled independently."""
    _, _, sid_a, _, _ = manager.exec_command("sleep 60", yield_time_ms=500)
    _, _, sid_b, _, _ = manager.exec_command("sleep 60", yield_time_ms=500)

    assert sid_a is not None
    assert sid_b is not None
    assert sid_a != sid_b

    # Both sessions still alive
    _, _, sid_pa, _, _ = manager.write_stdin(sid_a, yield_time_ms=5_000)
    assert sid_pa == sid_a

    _, _, sid_pb, _, _ = manager.write_stdin(sid_b, yield_time_ms=5_000)
    assert sid_pb == sid_b

    # Clean up both
    manager.write_stdin(sid_a, chars="\x03", yield_time_ms=1_000)
    manager.write_stdin(sid_b, chars="\x03", yield_time_ms=1_000)


# ──────────────────────────────────────────────────────────────────────────────
# Observation schema
# ──────────────────────────────────────────────────────────────────────────────


def test_observation_running_has_session_id_not_exit_code():
    obs = InteractiveTerminalObservation.create(
        output="partial output",
        wall_time_seconds=1.5,
        session_id=7,
        exit_code=None,
    )
    assert obs.session_id == 7
    assert obs.exit_code is None
    assert "session_id=7" in obs.text
    assert "still running" in obs.text.lower()


def test_observation_done_has_exit_code_not_session_id():
    obs = InteractiveTerminalObservation.create(
        output="done output",
        wall_time_seconds=3.0,
        session_id=None,
        exit_code=0,
    )
    assert obs.session_id is None
    assert obs.exit_code == 0
    assert "exit_code=0" in obs.text
    assert "completed" in obs.text.lower()


def test_observation_original_token_count():
    # The manager computes this pre-truncation and passes it as a parameter.
    obs = InteractiveTerminalObservation.create(
        output="x" * 400,
        wall_time_seconds=1.0,
        session_id=None,
        exit_code=0,
        original_token_count=100,  # 400 chars / 4 ≈ 100 tokens
    )
    assert obs.original_token_count == 100


# ──────────────────────────────────────────────────────────────────────────────
# Full tool execution via ToolDefinition.__call__
# ──────────────────────────────────────────────────────────────────────────────


def test_full_tool_exec_command_completes(tmp_dir):
    state = _conv_state(tmp_dir)
    tools = InteractiveTerminalToolSet.create(state)
    exec_tool = next(t for t in tools if t.name == "exec_command")

    obs = exec_tool(ExecCommandAction(cmd="echo codex_parity", yield_time_ms=5_000))

    assert isinstance(obs, InteractiveTerminalObservation)
    assert obs.exit_code == 0
    assert obs.session_id is None
    assert "codex_parity" in obs.output


@_unix_only
def test_full_tool_write_stdin_polls(tmp_dir):
    state = _conv_state(tmp_dir)
    tools = InteractiveTerminalToolSet.create(state)
    exec_tool = next(t for t in tools if t.name == "exec_command")
    write_tool = next(t for t in tools if t.name == "write_stdin")

    # Start slow command
    exec_obs = exec_tool(ExecCommandAction(cmd="sleep 60", yield_time_ms=500))
    assert isinstance(exec_obs, InteractiveTerminalObservation)
    assert exec_obs.session_id is not None

    # Poll it
    poll_obs = write_tool(
        WriteStdinAction(session_id=exec_obs.session_id, yield_time_ms=5_000)
    )
    assert isinstance(poll_obs, InteractiveTerminalObservation)
    assert poll_obs.session_id == exec_obs.session_id
    assert poll_obs.exit_code is None

    # Interrupt
    kill_obs = write_tool(
        WriteStdinAction(
            session_id=exec_obs.session_id, chars="\x03", yield_time_ms=2_000
        )
    )
    assert isinstance(kill_obs, InteractiveTerminalObservation)
    assert kill_obs.exit_code is not None
