"""AGENT-hook evaluators do not inherit ambient plugin hooks."""

import json

import pytest

from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.hooks.config import HookDefinition, HookType
from openhands.sdk.hooks.executor import HookExecutor
from openhands.sdk.hooks.types import HookDecision, HookEvent, HookEventType
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.plugin import (
    discovery,
    install_plugin,
    installed,
    load_available_plugins,
)
from openhands.sdk.testing import TestLLM
from tests.command_utils import python_command


@pytest.fixture
def ambient_hook_plugin(tmp_path, monkeypatch, request):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    user_dirs = [tmp_path / "user-agents", tmp_path / "user-openhands"]
    store = tmp_path / "installed"
    monkeypatch.setattr(discovery, "USER_PLUGINS_DIRS", user_dirs)
    monkeypatch.setattr(installed, "DEFAULT_INSTALLED_PLUGINS_DIR", store)
    locations = {
        "project-openhands": workspace / ".openhands" / "plugins" / "probe",
        "project-agents": workspace / ".agents" / "plugins" / "probe",
        "user-agents": user_dirs[0] / "probe",
        "user-openhands": user_dirs[1] / "probe",
        "installed": tmp_path / "source-plugin",
    }
    plugin = locations[request.param]
    manifest = plugin / ".plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"name": "evaluator-probe", "version": "1.0.0"}), encoding="utf-8"
    )
    marker = tmp_path / "hooks.jsonl"
    command = python_command(
        "import pathlib, sys; "
        f"pathlib.Path({str(marker)!r}).open('a', encoding='utf-8')"
        ".write(sys.stdin.read().replace(chr(10), ' ') + chr(10))"
    )
    hooks = plugin / "hooks" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(
        json.dumps(
            {
                event: [
                    {"matcher": "*", "hooks": [{"type": "command", "command": command}]}
                ]
                for event in ("SessionStart", "SessionEnd")
            }
        ),
        encoding="utf-8",
    )
    skills = plugin / "skills"
    skills.mkdir()
    (skills / "probe.md").write_text(
        "---\nname: evaluator-probe-skill\n---\nProbe.", encoding="utf-8"
    )
    if request.param == "installed":
        install_plugin(str(plugin), installed_dir=store)
    assert "evaluator-probe" in load_available_plugins(
        work_dir=workspace, include_user=True, include_project=True
    )
    return workspace, marker


def scripted_llm(decision="allow"):
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[
                    TextContent(text=json.dumps({"decision": decision, "reason": "ok"}))
                ],
            )
        ]
    )


@pytest.mark.parametrize(
    "ambient_hook_plugin",
    [
        "project-openhands",
        "project-agents",
        "user-agents",
        "user-openhands",
        "installed",
    ],
    indirect=True,
)
@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_agent_evaluator_ignores_ambient_plugin_hooks(ambient_hook_plugin, decision):
    workspace, marker = ambient_hook_plugin
    executor = HookExecutor(working_dir=str(workspace), llm=scripted_llm(decision))

    result = executor._execute_agent_hook(
        HookDefinition(type=HookType.AGENT, system_prompt="Evaluate."),
        HookEvent(event_type=HookEventType.STOP, session_id="parent"),
    )

    assert result.decision == HookDecision(decision)
    assert result.error is None
    assert not marker.exists()

    conversation = LocalConversation(
        agent=Agent(llm=scripted_llm(), tools=[]), workspace=workspace, visualizer=None
    )
    try:
        conversation.send_message("Start an ordinary conversation.")
        assert conversation.agent.agent_context is not None
        assert "evaluator-probe-skill" in {
            skill.name for skill in conversation.agent.agent_context.skills
        }
    finally:
        conversation.close()

    events = [
        json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event_type"] for event in events] == ["SessionStart", "SessionEnd"]
    assert {event["session_id"] for event in events} == {str(conversation.id)}


@pytest.mark.parametrize("ambient_hook_plugin", ["project-openhands"], indirect=True)
def test_ambient_agent_stop_hook_does_not_start_nested_evaluators(
    ambient_hook_plugin, monkeypatch
):
    workspace, marker = ambient_hook_plugin
    hooks_path = workspace / ".openhands/plugins/probe/hooks/hooks.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    hooks["Stop"] = [
        {
            "hooks": [
                {"type": "agent", "system_prompt": "Evaluate.", "max_iterations": 1}
            ]
        }
    ]
    hooks_path.write_text(json.dumps(hooks), encoding="utf-8")
    created = []
    initialize = LocalConversation.__init__

    def record_conversation(self, *args, **kwargs):
        created.append(self)
        if len(created) > 1:
            raise RuntimeError("An ambient Stop hook started a nested evaluator")
        initialize(self, *args, **kwargs)

    monkeypatch.setattr(LocalConversation, "__init__", record_conversation)
    result = HookExecutor(
        working_dir=str(workspace), llm=scripted_llm()
    )._execute_agent_hook(
        HookDefinition(type=HookType.AGENT, system_prompt="Evaluate."),
        HookEvent(event_type=HookEventType.STOP, session_id="parent"),
    )

    assert result.decision == HookDecision.ALLOW
    assert result.error is None
    assert len(created) == 1
    assert not marker.exists()
