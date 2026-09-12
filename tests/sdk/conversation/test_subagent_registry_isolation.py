import json
from contextlib import closing
from pathlib import Path

import pytest

from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.persistence_const import BASE_STATE
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.plugin import PluginSource
from openhands.sdk.subagent.registry import get_agent_factory
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.testing import TestLLM
from openhands.tools.task.definition import TaskToolSet


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


def _write_agent(project: Path, description: str) -> None:
    directory = project / ".agents" / "agents"
    directory.mkdir(parents=True)
    (directory / "shared.md").write_text(
        "---\n"
        "name: shared\n"
        f"description: {description}\n"
        "---\n\n"
        f"Prompt for {description}.\n"
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_file_agents_are_isolated_per_conversation(
    tmp_path: Path, reverse: bool
) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    _write_agent(project_a, "definition-a")
    _write_agent(project_b, "definition-b")
    projects = [project_a, project_b]
    if reverse:
        projects.reverse()

    conversations = [
        LocalConversation(
            agent=Agent(llm=TestLLM.from_messages([]), tools=[]),
            workspace=project,
            visualizer=None,
        )
        for project in projects
    ]
    try:
        for conversation in conversations:
            conversation.send_message("Prepare the conversation")

        descriptions = {
            Path(conversation.workspace.working_dir).name: (
                conversation._agent_registry.get_agent_factory(
                    "shared"
                ).definition.description
            )
            for conversation in conversations
        }
        assert descriptions == {
            "project-a": "definition-a",
            "project-b": "definition-b",
        }
        for conversation in conversations:
            description = descriptions[Path(conversation.workspace.working_dir).name]
            task_tool = TaskToolSet.create(conversation._state)[0]
            assert description in task_tool.description
            worker = conversation._agent_registry.get_agent_factory(
                "shared"
            ).factory_func(conversation.agent.llm)
            assert worker.agent_context is not None
            assert (
                worker.agent_context.system_message_suffix
                == f"Prompt for {description}."
            )
    finally:
        for conversation in conversations:
            conversation.close()


@pytest.mark.parametrize("initialize", [False, True])
@pytest.mark.parametrize("custom_file_store", [False, True])
def test_close_reopen_keeps_forwarded_definitions(
    tmp_path: Path, initialize: bool, custom_file_store: bool
) -> None:
    file_store = InMemoryFileStore() if custom_file_store else None
    persistence_dir = str(tmp_path / "conversations")
    definition = AgentDefinition(
        name="forwarded", description="resume-visible", system_prompt="Saved prompt"
    )
    with closing(
        LocalConversation(
            agent=Agent(llm=TestLLM.from_messages([]), tools=[]),
            workspace=tmp_path,
            persistence_dir=persistence_dir,
            file_store=file_store,
            agent_definitions=[definition],
            visualizer=None,
        )
    ) as conversation:
        conversation_id = conversation.id
        if initialize:
            conversation.send_message("Prepare original")
            assert (
                conversation._agent_registry.get_agent_factory("forwarded").definition
                == definition
            )
            with pytest.raises(ValueError, match="Unknown agent 'forwarded'"):
                get_agent_factory("forwarded")

    with closing(
        LocalConversation(
            conversation_id=conversation_id,
            agent=None,
            workspace=tmp_path,
            persistence_dir=persistence_dir,
            file_store=file_store,
            visualizer=None,
        )
    ) as resumed:
        resumed.send_message("Prepare resumed")
        assert "resume-visible" in TaskToolSet.create(resumed._state)[0].description
        factory = resumed._agent_registry.get_agent_factory("forwarded")
        assert factory.definition == definition
        worker = factory.factory_func(resumed.agent.llm)
        assert worker.agent_context is not None
        assert worker.agent_context.system_message_suffix == "Saved prompt"
        with pytest.raises(ValueError, match="Unknown agent 'forwarded'"):
            get_agent_factory("forwarded")


@pytest.mark.parametrize("mode", ["replace", "clear", "legacy"])
def test_resume_forwarded_definition_updates(tmp_path: Path, mode: str) -> None:
    file_store = InMemoryFileStore()
    with closing(
        LocalConversation(
            agent=Agent(llm=TestLLM.from_messages([]), tools=[]),
            workspace=tmp_path,
            file_store=file_store,
            agent_definitions=[AgentDefinition(name="forwarded", description="old")],
            visualizer=None,
        )
    ) as conversation:
        conversation_id = conversation.id

    replacement = AgentDefinition(name="forwarded", description="replacement")
    definitions = [replacement] if mode == "replace" else []
    if mode == "legacy":
        saved = json.loads(file_store.read(BASE_STATE))
        del saved["agent_definitions"]
        file_store.write(BASE_STATE, json.dumps(saved))

    # Reopen again without arguments to verify the update itself was saved.
    for forwarded in [None if mode == "legacy" else definitions, None]:
        with closing(
            LocalConversation(
                agent=None,
                conversation_id=conversation_id,
                workspace=tmp_path,
                file_store=file_store,
                agent_definitions=forwarded,
                visualizer=None,
            )
        ) as resumed:
            resumed.send_message("Prepare resumed")
            if mode == "replace":
                assert (
                    resumed._agent_registry.get_agent_factory("forwarded").definition
                    == replacement
                )
            else:
                with pytest.raises(ValueError, match="Unknown agent 'forwarded'"):
                    resumed._agent_registry.get_agent_factory("forwarded")


def test_plugin_overrides_forwarded_project_definition(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    manifest = plugin / ".plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(json.dumps({"name": "review-plugin"}))
    agents = plugin / "agents"
    agents.mkdir()
    (agents / "shared.md").write_text(
        "---\nname: shared\ndescription: plugin winner\n---\nPlugin prompt.\n"
    )
    definition = AgentDefinition(
        name="shared", description="project loser", level="project"
    )
    with closing(
        LocalConversation(
            agent=Agent(llm=TestLLM.from_messages([]), tools=[]),
            workspace=tmp_path,
            plugins=[PluginSource(source=str(plugin))],
            agent_definitions=[definition],
            visualizer=None,
        )
    ) as conversation:
        conversation.send_message("Prepare the conversation")
        assert "plugin winner" in TaskToolSet.create(conversation._state)[0].description
        assert (
            "project loser"
            not in TaskToolSet.create(conversation._state)[0].description
        )


def test_bad_forwarded_definition_does_not_block_valid_agents(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with closing(
        LocalConversation(
            agent=Agent(llm=TestLLM.from_messages([]), tools=[]),
            workspace=tmp_path,
            agent_definitions=[
                AgentDefinition(
                    name="broken", description="bad", skills=["missing-skill"]
                ),
                AgentDefinition(
                    name="valid", description="survives", system_prompt="Valid prompt"
                ),
            ],
            visualizer=None,
        )
    ) as conversation:
        assert "Failed to register" not in caplog.text
        conversation.send_message("Prepare the conversation")
        assert "Failed to register agent definition 'broken'" in caplog.text
        assert "survives" in TaskToolSet.create(conversation._state)[0].description


@pytest.mark.parametrize("initialize", [False, True])
def test_fork_keeps_forwarded_definitions(tmp_path: Path, initialize: bool) -> None:
    with closing(
        LocalConversation(
            agent=Agent(llm=TestLLM.from_messages([]), tools=[]),
            workspace=tmp_path,
            agent_definitions=[
                AgentDefinition(name="forwarded", description="fork-visible")
            ],
            visualizer=None,
        )
    ) as conversation:
        if initialize:
            conversation.send_message("Prepare parent")
        with closing(conversation.fork()) as fork:
            fork.send_message("Prepare fork")
            assert "fork-visible" in TaskToolSet.create(fork._state)[0].description
