from pathlib import Path
from types import SimpleNamespace
from typing import cast

from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event.llm_convertible import ObservationEvent
from openhands.sdk.llm import TextContent
from openhands.sdk.skills import Skill, SkillResources, SkillRuntime
from openhands.sdk.tool.builtins import (
    BUILT_IN_TOOL_CLASSES,
    SkillsListAction,
    SkillsListObservation,
    SkillsListTool,
    SkillsReadAction,
    SkillsReadObservation,
    SkillsReadTool,
)


def make_runtime(tmp_path: Path) -> SkillRuntime:
    root = tmp_path / "reader"
    (root / "references").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "SKILL.md").write_text("# Reader", encoding="utf-8")
    (root / "references" / "guide.md").write_text("guide", encoding="utf-8")
    (root / "scripts" / "run.py").write_text("print('ok')", encoding="utf-8")
    return SkillRuntime(
        [
            Skill(
                name="reader",
                content="# Reader",
                description="Read workflow references",
                source=str(root / "SKILL.md"),
                is_agentskills_format=True,
                resources=SkillResources(
                    skill_root=str(root),
                    references=["guide.md"],
                    scripts=["run.py"],
                ),
            )
        ]
    )


def test_tools_are_registered_and_create_without_runtime_params():
    assert BUILT_IN_TOOL_CLASSES["SkillsListTool"] is SkillsListTool
    assert BUILT_IN_TOOL_CLASSES["SkillsReadTool"] is SkillsReadTool
    assert len(SkillsListTool.create()) == 1
    assert len(SkillsReadTool.create()) == 1


def make_conversation(runtime: SkillRuntime):
    skills = [entry.skill for entry in runtime.list()]
    return SimpleNamespace(
        state=SimpleNamespace(
            agent=SimpleNamespace(
                agent_context=SimpleNamespace(skills=skills),
            )
        )
    )


def test_list_and_read_tools_execute(tmp_path):
    runtime = make_runtime(tmp_path)
    conversation = make_conversation(runtime)
    (list_tool,) = SkillsListTool.create()
    (read_tool,) = SkillsReadTool.create()

    listed = cast(
        SkillsListObservation,
        list_tool(
            SkillsListAction(query="workflow"),
            conversation=cast(LocalConversation, conversation),
        ),
    )
    assert listed.skills == ["reader"]
    assert listed.text == "reader"

    empty = cast(SkillsListObservation, list_tool(SkillsListAction()))
    assert empty.skills == []
    assert empty.text == "No skills found."

    read = cast(
        SkillsReadObservation,
        read_tool(
            SkillsReadAction(skill_name="reader", path="references/guide.md"),
            conversation=cast(LocalConversation, conversation),
        ),
    )
    assert read.skill_name == "reader"
    assert read.path == "references/guide.md"
    assert read.contents == "guide"
    assert read.text == "guide"
    assert isinstance(read.to_llm_content[0], TextContent)
    assert read.to_llm_content[0].text == "guide"
    assert read.model_dump(mode="json")["content"][0]["text"] == "guide"

    message = ObservationEvent(
        observation=read,
        action_id="action-id",
        tool_name="skills_read",
        tool_call_id="tool-call-id",
    ).to_llm_message()
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "guide"

    denied = cast(
        SkillsReadObservation,
        read_tool(
            SkillsReadAction(skill_name="reader", path="scripts/run.py"),
            conversation=cast(LocalConversation, conversation),
        ),
    )
    assert denied.is_error is True
    assert denied.contents == ""
    assert "Skill scripts are executable helpers" in denied.text


def test_tools_declare_read_only_resources(tmp_path):
    make_runtime(tmp_path)
    (list_tool,) = SkillsListTool.create()
    (read_tool,) = SkillsReadTool.create()

    assert list_tool.declared_resources(SkillsListAction()).declared is True
    resource = read_tool.declared_resources(
        SkillsReadAction(skill_name="reader", path="references/guide.md")
    )
    assert resource.declared is True
    assert resource.keys == ("skill:reader:references/guide.md",)


def test_read_action_accepts_canonical_and_legacy_skill_name(tmp_path):
    runtime = make_runtime(tmp_path)
    conversation = make_conversation(runtime)
    (read_tool,) = SkillsReadTool.create()
    canonical = SkillsReadAction.model_validate(
        {"skill_name": "reader", "path": "references/guide.md"}
    )
    legacy = SkillsReadAction.model_validate(
        {"name": "reader", "path": "references/guide.md"}
    )

    assert canonical == legacy
    schema = SkillsReadAction.to_mcp_schema()
    assert "skill_name" in schema["properties"]
    assert "name" not in schema["properties"]
    assert canonical.model_dump(exclude={"kind"}) == {
        "skill_name": "reader",
        "path": "references/guide.md",
    }
    assert read_tool.declared_resources(canonical) == read_tool.declared_resources(
        legacy
    )

    canonical_result = cast(
        SkillsReadObservation,
        read_tool(canonical, conversation=cast(LocalConversation, conversation)),
    )
    legacy_result = cast(
        SkillsReadObservation,
        read_tool(legacy, conversation=cast(LocalConversation, conversation)),
    )
    assert canonical_result == legacy_result
