"""Tests for AgentContext serialization and deserialization."""

import json

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.skills import (
    KeywordTrigger,
    Skill,
    TaskTrigger,
)
from openhands.sdk.context.skills.types import InputMetadata


def test_agent_context_serialization_roundtrip():
    """Ensure AgentContext round-trips through dict and JSON serialization."""

    repo_skill = Skill(
        name="repo-guidelines",
        content="Repository guidelines",
        source="repo.md",
        trigger=None,
    )
    knowledge_skill = Skill(
        name="python-help",
        content="Use type hints in Python code",
        source="knowledge.md",
        trigger=KeywordTrigger(keywords=["python"]),
    )
    task_skill = Skill(
        name="run-task",
        content="Execute the task with ${param}",
        source="task.md",
        trigger=TaskTrigger(triggers=["run"]),
        inputs=[InputMetadata(name="param", description="Task parameter")],
    )

    context = AgentContext(
        skills=[repo_skill, knowledge_skill, task_skill],
        system_message_suffix="System suffix",
        user_message_suffix="User suffix",
    )

    serialized = context.model_dump()
    assert serialized["system_message_suffix"] == "System suffix"
    assert serialized["user_message_suffix"] == "User suffix"
    # First skill has trigger=None (always-active), others have specific triggers
    assert serialized["skills"][0]["trigger"] is None
    assert serialized["skills"][1]["trigger"]["type"] == "keyword"
    assert serialized["skills"][2]["trigger"]["type"] == "task"

    json_str = context.model_dump_json()
    parsed = json.loads(json_str)
    assert parsed["system_message_suffix"] == "System suffix"
    assert parsed["user_message_suffix"] == "User suffix"
    assert parsed["skills"][2]["inputs"][0]["name"] == "param"

    deserialized_from_dict = AgentContext.model_validate(serialized)
    assert isinstance(deserialized_from_dict.skills[0], Skill)
    assert deserialized_from_dict.skills[0].trigger is None
    assert deserialized_from_dict.skills[0] == repo_skill
    assert isinstance(deserialized_from_dict.skills[1], Skill)
    assert isinstance(deserialized_from_dict.skills[1].trigger, KeywordTrigger)
    assert deserialized_from_dict.skills[1] == knowledge_skill
    assert isinstance(deserialized_from_dict.skills[2], Skill)
    assert isinstance(deserialized_from_dict.skills[2].trigger, TaskTrigger)
    assert deserialized_from_dict.skills[2] == task_skill
    assert deserialized_from_dict.system_message_suffix == "System suffix"
    assert deserialized_from_dict.user_message_suffix == "User suffix"

    deserialized_from_json = AgentContext.model_validate_json(json_str)
    assert isinstance(deserialized_from_json.skills[0], Skill)
    assert deserialized_from_json.skills[0].trigger is None
    assert deserialized_from_json.skills[0] == repo_skill
    assert isinstance(deserialized_from_json.skills[1], Skill)
    assert isinstance(deserialized_from_json.skills[1].trigger, KeywordTrigger)
    assert deserialized_from_json.skills[1] == knowledge_skill
    assert isinstance(deserialized_from_json.skills[2], Skill)
    assert isinstance(deserialized_from_json.skills[2].trigger, TaskTrigger)
    assert deserialized_from_json.skills[2] == task_skill
    assert deserialized_from_json.model_dump() == serialized
