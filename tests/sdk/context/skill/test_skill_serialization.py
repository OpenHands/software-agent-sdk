"""Tests for skill serialization using trigger composition."""

import json

from pydantic import BaseModel, Field

from openhands.sdk.context.skills import (
    KeywordTrigger,
    Skill,
    TaskTrigger,
)
from openhands.sdk.context.skills.types import InputMetadata
from openhands.sdk.utils.models import OpenHandsModel


def test_repo_skill_serialization():
    """Test Skill with trigger=None (always-active) serialization."""
    # Create a Skill with trigger=None (always-active)
    repo_skill = Skill(
        name="test-repo",
        content="Repository-specific instructions",
        source="test-repo.md",
        trigger=None,
    )

    # Test serialization
    serialized = repo_skill.model_dump()
    assert serialized["trigger"] is None
    assert serialized["name"] == "test-repo"
    assert serialized["content"] == "Repository-specific instructions"
    assert serialized["source"] == "test-repo.md"
    assert serialized["mcp_tools"] is None

    # Test JSON serialization
    json_str = repo_skill.model_dump_json()
    assert isinstance(json_str, str)
    parsed = json.loads(json_str)
    assert parsed["trigger"] is None

    # Test deserialization
    deserialized = Skill.model_validate(serialized)
    assert deserialized.trigger is None
    assert deserialized == repo_skill


def test_knowledge_skill_serialization():
    """Test Skill with KeywordTrigger serialization and deserialization."""
    # Create a Skill with KeywordTrigger
    knowledge_skill = Skill(
        name="test-knowledge",
        content="Knowledge-based instructions",
        source="test-knowledge.md",
        trigger=KeywordTrigger(keywords=["python", "testing"]),
    )

    # Test serialization
    serialized = knowledge_skill.model_dump()
    assert serialized["trigger"]["type"] == "keyword"
    assert serialized["name"] == "test-knowledge"
    assert serialized["content"] == "Knowledge-based instructions"
    assert serialized["trigger"]["keywords"] == ["python", "testing"]

    # Test JSON serialization
    json_str = knowledge_skill.model_dump_json()
    assert isinstance(json_str, str)
    parsed = json.loads(json_str)
    assert parsed["trigger"]["type"] == "keyword"

    # Test deserialization
    deserialized = Skill.model_validate(serialized)
    assert deserialized == knowledge_skill


def test_task_skill_serialization():
    """Test Skill with TaskTrigger serialization and deserialization."""
    # Create a Skill with TaskTrigger
    task_skill = Skill(
        name="test-task",
        content="Task-based instructions with ${variable}",
        source="test-task.md",
        trigger=TaskTrigger(triggers=["task", "automation"]),
        inputs=[
            InputMetadata(name="variable", description="A test variable"),
        ],
    )

    # Test serialization
    serialized = task_skill.model_dump()
    assert serialized["trigger"]["type"] == "task"
    assert serialized["name"] == "test-task"
    assert "Task-based instructions with ${variable}" in serialized["content"]
    assert serialized["trigger"]["triggers"] == ["task", "automation"]
    assert len(serialized["inputs"]) == 1
    assert serialized["inputs"][0]["name"] == "variable"

    # Test JSON serialization
    json_str = task_skill.model_dump_json()
    assert isinstance(json_str, str)
    parsed = json.loads(json_str)
    assert parsed["trigger"]["type"] == "task"

    # Test deserialization
    deserialized = Skill.model_validate(serialized)
    assert deserialized == task_skill


def test_skill_union_serialization_roundtrip():
    """Test complete serialization roundtrip for all trigger types."""
    # Test data for each trigger type
    test_cases = [
        Skill(
            name="repo-test",
            content="Repo content",
            source="repo.md",
            trigger=None,
        ),
        Skill(
            name="knowledge-test",
            content="Knowledge content",
            source="knowledge.md",
            trigger=KeywordTrigger(keywords=["test"]),
        ),
        Skill(
            name="task-test",
            content="Task content with ${var}",
            source="task.md",
            trigger=TaskTrigger(triggers=["task"]),
            inputs=[InputMetadata(name="var", description="Test variable")],
        ),
    ]

    for original_skill in test_cases:
        # Serialize to dict
        serialized = original_skill.model_dump()

        # Serialize to JSON string
        json_str = original_skill.model_dump_json()

        # Deserialize from dict
        deserialized_from_dict = Skill.model_validate(serialized)

        # Deserialize from JSON string
        deserialized_from_json = Skill.model_validate_json(json_str)

        # Verify all versions are equivalent
        assert deserialized_from_dict == original_skill
        assert deserialized_from_json == original_skill


def test_skill_union_polymorphic_list():
    """Test that a list of Skills can contain different trigger types."""
    # Create a list with different trigger types
    skills = [
        Skill(
            name="repo1",
            content="Repo content",
            source="repo1.md",
            trigger=None,
        ),
        Skill(
            name="knowledge1",
            content="Knowledge content",
            source="knowledge1.md",
            trigger=KeywordTrigger(keywords=["test"]),
        ),
        Skill(
            name="task1",
            content="Task content",
            source="task1.md",
            trigger=TaskTrigger(triggers=["task"]),
        ),
    ]

    # Serialize the list
    serialized_list = [skill.model_dump() for skill in skills]

    # Verify each item has correct trigger type
    assert serialized_list[0]["trigger"] is None  # Always-active skill
    assert serialized_list[1]["trigger"]["type"] == "keyword"
    assert serialized_list[2]["trigger"]["type"] == "task"

    # Test JSON serialization of the list
    json_str = json.dumps(serialized_list)
    parsed_list = json.loads(json_str)

    assert len(parsed_list) == 3
    assert parsed_list[0]["trigger"] is None  # Always-active skill
    assert parsed_list[1]["trigger"]["type"] == "keyword"
    assert parsed_list[2]["trigger"]["type"] == "task"

    # reconstruct the list from serialized data
    deserialized_list = [Skill.model_validate(item) for item in serialized_list]

    assert len(deserialized_list) == 3
    assert deserialized_list[0].trigger is None
    assert isinstance(deserialized_list[1].trigger, KeywordTrigger)
    assert isinstance(deserialized_list[2].trigger, TaskTrigger)
    assert deserialized_list[0] == skills[0]
    assert deserialized_list[1] == skills[1]
    assert deserialized_list[2] == skills[2]


def test_discriminated_union_with_openhands_model():
    """Test trigger discrimination functionality with OpenHandsModel."""

    class TestModel(OpenHandsModel):
        skills: list[Skill] = Field(default_factory=list)

    # Create test data with different trigger types
    test_data = {
        "skills": [
            {
                "kind": "Skill",
                "name": "test-repo",
                "content": "Repo content",
                "source": "repo.md",
                "trigger": None,  # Always-active skill
                "mcp_tools": None,
            },
            {
                "kind": "Skill",
                "name": "test-knowledge",
                "content": "Knowledge content",
                "source": "knowledge.md",
                "trigger": {"type": "keyword", "keywords": ["test"]},
            },
            {
                "kind": "Skill",
                "name": "test-task",
                "content": "Task content",
                "source": "task.md",
                "trigger": {"type": "task", "triggers": ["task"]},
                "inputs": [],
            },
        ]
    }

    # Validate the model - this tests the trigger discrimination
    model = TestModel.model_validate(test_data)

    # Verify each skill was correctly discriminated
    assert len(model.skills) == 3
    assert model.skills[0].trigger is None
    assert isinstance(model.skills[1].trigger, KeywordTrigger)
    assert isinstance(model.skills[2].trigger, TaskTrigger)

    # Verify trigger types are correct
    # First skill is always-active (trigger is None)
    assert model.skills[1].trigger.type == "keyword"
    assert model.skills[2].trigger.type == "task"


def test_discriminated_union_with_pydantic_model():
    """Test trigger discrimination functionality with Pydantic BaseModel."""

    class TestModel(BaseModel):
        skills: list[Skill] = Field(default_factory=list)

    # Create test data with different trigger types
    test_data = {
        "skills": [
            {
                "name": "test-repo",
                "content": "Repo content",
                "source": "repo.md",
                "trigger": None,  # Always-active skill
                "mcp_tools": None,
            },
            {
                "name": "test-knowledge",
                "content": "Knowledge content",
                "source": "knowledge.md",
                "trigger": {"type": "keyword", "keywords": ["test"]},
            },
            {
                "name": "test-task",
                "content": "Task content",
                "source": "task.md",
                "trigger": {"type": "task", "triggers": ["task"]},
                "inputs": [],
            },
        ]
    }

    # Validate the model - this tests the trigger discrimination
    model = TestModel.model_validate(test_data)

    # Verify each skill was correctly discriminated
    assert len(model.skills) == 3
    assert model.skills[0].trigger is None
    assert isinstance(model.skills[1].trigger, KeywordTrigger)
    assert isinstance(model.skills[2].trigger, TaskTrigger)

    # Verify trigger types are correct
    # First skill is always-active (trigger is None)
    assert model.skills[1].trigger.type == "keyword"
    assert model.skills[2].trigger.type == "task"
