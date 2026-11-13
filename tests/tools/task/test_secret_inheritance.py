"""Real task lifecycles and terminals; only the remote model is scripted."""

import json
import logging
import os
import re
import secrets
from contextlib import closing
from pathlib import Path

import pytest

from openhands.sdk import Agent, LocalConversation, Tool
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.tools.task.definition import TaskAction
from openhands.tools.task.impl import TaskExecutor
from openhands.tools.task.manager import TaskManager
from openhands.tools.terminal import TerminalTool


@pytest.fixture(autouse=True)
def isolated_registry_and_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _reset_registry_for_tests()
    # Do not pass the developer's credentials or shell startup files to the terminal.
    for name in list(os.environ):
        if name.upper() not in {
            "PATH",
            "LANG",
            "LC_ALL",
            "SYSTEMROOT",
            "COMSPEC",
            "PATHEXT",
            "TEMP",
            "TMP",
        }:
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HISTFILE", os.devnull)
    yield
    _reset_registry_for_tests()


def _call(name: str, **arguments: str) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id=f"call_{name}",
                name=name,
                arguments=json.dumps(arguments),
                origin="completion",
            )
        ],
    )


@pytest.mark.parametrize("resume", [False, True], ids=["initial", "resume"])
@pytest.mark.parametrize("has_secret", [True, False], ids=["authorized", "empty"])
def test_task_secret_delivery_and_masking(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    resume: bool,
    has_secret: bool,
) -> None:
    token = secrets.token_hex(32)
    command = (
        'if [ -n "${TASK_PROBE_TOKEN:-}" ]; then echo PROBE_PRESENT; '
        "else echo PROBE_ABSENT; fi; "
        'printf "MASK_PROBE=%s\\n" "$TASK_PROBE_TOKEN"'
    )
    if os.name == "nt":
        command = (
            'if ($env:TASK_PROBE_TOKEN) { Write-Output "PROBE_PRESENT" } '
            'else { Write-Output "PROBE_ABSENT" }; '
            'Write-Output "MASK_PROBE=$env:TASK_PROBE_TOKEN"'
        )
    script: list[Message | Exception] = [
        _call("terminal", command=command),
        _call("finish", message="done"),
    ]
    if resume:
        script.insert(0, _call("finish", message="ready to resume"))
    llm = TestLLM.from_messages(script)
    register_agent(
        "secret-probe",
        lambda llm: Agent(
            llm=llm,
            tools=[
                Tool(
                    name=TerminalTool.name,
                    params={
                        "terminal_type": "powershell"
                        if os.name == "nt"
                        else "subprocess"
                    },
                )
            ],
        ),
        "Probe inherited secrets with a real terminal",
    )
    manager = TaskManager()
    executor = TaskExecutor(manager)
    caplog.set_level(logging.DEBUG, logger="openhands")
    with closing(
        LocalConversation(
            agent=Agent(llm=llm),
            workspace=tmp_path,
            persistence_dir=tmp_path / "persist",
            visualizer=None,
            secrets={"TASK_PROBE_TOKEN": token} if has_secret else {},
        )
    ) as parent:
        assert isinstance(parent.state.secret_registry, SecretRegistry)
        try:
            first = executor(
                TaskAction(prompt="probe", subagent_type="secret-probe"), parent
            )
            assert first.status == "completed", first
            if resume:
                result = executor(
                    TaskAction(
                        prompt="probe again",
                        subagent_type="secret-probe",
                        resume=first.task_id,
                    ),
                    parent,
                )
                assert result.status == "completed", result
                assert result.task_id == first.task_id
            assert parent.state.persistence_dir is not None
            subagents = Path(parent.state.persistence_dir) / "subagents"
            states = list(subagents.rglob("base_state.json"))
            assert len(states) == 1
            event_files = list(subagents.rglob("events/*.json"))
            events = [json.loads(path.read_text()) for path in event_files]
            terminal_results = [
                event["observation"]
                for event in events
                if event.get("tool_name") == "terminal" and "observation" in event
            ]
            assert len(terminal_results) == 1
            observation = terminal_results[0]
            assert observation["exit_code"] == 0
            output = "\n".join(item["text"] for item in observation["content"])
            lines = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", output).splitlines()
            assert ("PROBE_PRESENT" if has_secret else "PROBE_ABSENT") in lines
            if has_secret:
                assert "MASK_PROBE=<secret-hidden>" in lines
            if resume:
                assert any(
                    "ready to resume" in path.read_text() for path in event_files
                )
            assert set(parent.state.secret_registry.secret_sources) == (
                {"TASK_PROBE_TOKEN"} if has_secret else set()
            )
        finally:
            executor.close()
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err + caplog.text
    for path in (tmp_path / "persist").rglob("*"):
        if path.is_file():
            assert token.encode() not in path.read_bytes(), str(path)


@pytest.mark.parametrize("resume", [False, True], ids=["initial", "resume"])
def test_sources_available_before_agent_initialization(tmp_path: Path, resume: bool):
    token = secrets.token_hex(32)
    register_agent("probe", lambda llm: Agent(llm=llm), "Secret source probe")
    manager = TaskManager()
    with closing(
        LocalConversation(
            agent=Agent(llm=TestLLM.from_messages([])),
            workspace=tmp_path,
            persistence_dir=tmp_path / "persist",
            visualizer=None,
            secrets={"TASK_PROBE_TOKEN": token},
        )
    ) as parent:
        manager.attach_parent(parent)
        task = manager._create_task("probe", "probe")
        try:
            if resume:
                manager._evict_task(task)
                task = manager._resume_task(task.id, "probe")
            child = task.conversation
            assert child is not None
            # No send_message/run: sources must exist before lazy plugin/agent init.
            assert isinstance(child.state.secret_registry, SecretRegistry)
            assert child.state.secret_registry.secret_sources == (
                parent.state.secret_registry.secret_sources
            )
            child.update_secrets({"CHILD_ONLY": secrets.token_hex(32)})
            assert set(parent.state.secret_registry.secret_sources) == {
                "TASK_PROBE_TOKEN"
            }
        finally:
            manager._evict_task(task)
            manager.close()
