"""Windows-specific coverage for the PowerShell terminal backend."""

import os
import platform
import tempfile
import uuid
from collections.abc import Generator
from typing import cast

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool, register_tool
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal import TerminalAction, TerminalTool
from openhands.tools.terminal.impl import TerminalExecutor
from openhands.tools.terminal.terminal import TerminalSession, create_terminal_session


pytestmark = pytest.mark.skipif(
    platform.system() != "Windows",
    reason="Windows terminal tests only run on Windows",
)


@pytest.fixture
def temp_dir() -> Generator[str]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield tmp_dir


@pytest.fixture
def windows_session(temp_dir: str) -> Generator[TerminalSession]:
    session = create_terminal_session(work_dir=temp_dir)
    session.initialize()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def conversation(temp_dir: str) -> Generator[LocalConversation]:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    register_tool("TerminalTool", TerminalTool)
    agent = Agent(llm=llm, tools=[Tool(name="TerminalTool")])
    conversation = LocalConversation(agent=agent, workspace=temp_dir)
    conversation._ensure_agent_ready()
    try:
        yield conversation
    finally:
        conversation.close()


@pytest.fixture
def terminal_executor(conversation: LocalConversation) -> TerminalExecutor:
    terminal_tool = conversation.agent.tools_map["terminal"]
    return cast(TerminalExecutor, terminal_tool.executor)


def _normalize_path(path: str) -> str:
    return os.path.realpath(path).lower().replace("\\", "/")


def test_factory_auto_detects_windows_terminal(temp_dir: str) -> None:
    session = create_terminal_session(work_dir=temp_dir)
    try:
        assert type(session.terminal).__name__ == "WindowsTerminal"
        assert session.terminal.is_powershell()
    finally:
        session.close()


def test_forced_windows_backend_uses_powershell(temp_dir: str) -> None:
    session = create_terminal_session(work_dir=temp_dir, terminal_type="powershell")
    try:
        assert type(session.terminal).__name__ == "WindowsTerminal"
        assert session.terminal.is_powershell()
    finally:
        session.close()


def test_basic_command_execution(windows_session) -> None:
    obs = windows_session.execute(
        TerminalAction(command='Write-Output "Hello from Windows terminal"')
    )

    assert obs.exit_code == 0
    assert "Hello from Windows terminal" in obs.text


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        pytest.param('Write-Output "a\nb"', ["a", "b"], id="newline-in-string"),
        pytest.param(
            'if ($true) {\n    Write-Output "yes"\n}',
            ["yes"],
            id="if-block",
        ),
        pytest.param(
            'foreach ($i in 1..2) {\n    Write-Output "n$i"\n}',
            ["n1", "n2"],
            id="foreach-block",
        ),
        pytest.param(
            'Write-Output `\n    "cont"',
            ["cont"],
            id="backtick-continuation",
        ),
        pytest.param(
            '1..3 |\n    ForEach-Object { "v$_" }',
            ["v1", "v2", "v3"],
            id="pipeline-across-lines",
        ),
    ],
)
def test_multiline_powershell_command_executes(
    windows_session, command: str, expected: list[str]
) -> None:
    """Multiline statements must leave PowerShell's ">>" continuation prompt.

    Each case is a single statement spanning several lines, so it passes the
    multiple-command guard in ``TerminalSession.execute`` and reaches
    ``send_keys()``, which is what this PR fixes.
    """
    obs = windows_session.execute(TerminalAction(command=command))

    assert obs.exit_code == 0
    for token in expected:
        assert token in obs.text


def test_multiple_statements_rejected_like_unix(windows_session) -> None:
    """Two newline-separated statements are refused, as on the unix terminals.

    The guard lives in ``TerminalSession.execute``, which every terminal backend
    is wrapped in, so this rejection is not Windows-specific. It is what
    distinguishes multiple *statements* from a multi-line single statement.
    """
    obs = windows_session.execute(
        TerminalAction(command="Write-Output a\nWrite-Output b")
    )

    assert obs.is_error
    assert "Cannot execute multiple commands at once" in obs.text


def test_working_directory_updates_and_persists(windows_session, temp_dir: str) -> None:
    subdir = os.path.join(temp_dir, "subdir")
    os.makedirs(subdir, exist_ok=True)

    obs = windows_session.execute(TerminalAction(command=f'Set-Location "{subdir}"'))
    assert obs.exit_code == 0

    obs = windows_session.execute(TerminalAction(command="(Get-Location).Path"))
    assert _normalize_path(obs.text.strip()) == _normalize_path(subdir)
    assert windows_session.cwd.replace("\\", "/").lower() == _normalize_path(subdir)


def test_failed_powershell_command_reports_failure(windows_session) -> None:
    obs = windows_session.execute(TerminalAction(command="Get-Item __missing_path__"))

    assert obs.exit_code == 1


def test_native_exit_code_does_not_leak_to_next_command(windows_session) -> None:
    obs = windows_session.execute(
        TerminalAction(command='python -c "import sys; sys.exit(7)"')
    )
    assert obs.exit_code == 7

    obs = windows_session.execute(TerminalAction(command='Write-Output "ok"'))
    assert obs.exit_code == 0
    assert "ok" in obs.text


def test_terminal_executor_exports_conversation_secrets_in_powershell(
    conversation: LocalConversation,
    terminal_executor: TerminalExecutor,
) -> None:
    conversation.update_secrets({"API_KEY": "test-api-key"})

    obs = terminal_executor(
        TerminalAction(command="Write-Output $env:API_KEY"),
        conversation=conversation,
    )

    assert obs.exit_code == 0
    assert "<secret-hidden>" in obs.text


def test_terminal_tool_uses_windows_description(temp_dir: str) -> None:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    conv_state = ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=temp_dir),
    )

    tools = TerminalTool.create(conv_state, terminal_type="powershell")
    assert "PowerShell session" in tools[0].description
