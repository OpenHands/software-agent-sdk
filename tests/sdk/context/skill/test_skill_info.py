"""Tests for Skill.to_skill_info() and related methods."""

from typing import Literal, get_args

from openhands.sdk.context.skills import (
    KeywordTrigger,
    Skill,
    TaskTrigger,
)
from openhands.sdk.context.skills.skill import SkillInfo


SkillType = Literal["repo", "knowledge", "agentskills"]


class TestSkillGetSkillType:
    """Tests for Skill.get_skill_type() method."""

    def test_repo_skill_type(self):
        """Test that a skill with trigger=None returns 'repo' type."""
        skill = Skill(
            name="test-repo",
            content="Repository instructions",
            trigger=None,
        )
        assert skill.get_skill_type() == "repo"

    def test_knowledge_skill_type_with_keyword_trigger(self):
        """Test that a skill with KeywordTrigger returns 'knowledge' type."""
        skill = Skill(
            name="test-knowledge",
            content="Knowledge instructions",
            trigger=KeywordTrigger(keywords=["python", "testing"]),
        )
        assert skill.get_skill_type() == "knowledge"

    def test_knowledge_skill_type_with_task_trigger(self):
        """Test that a skill with TaskTrigger returns 'knowledge' type."""
        skill = Skill(
            name="test-task",
            content="Task instructions",
            trigger=TaskTrigger(triggers=["task"]),
        )
        assert skill.get_skill_type() == "knowledge"

    def test_agent_skill_type(self):
        """Test that an AgentSkills format skill returns 'agentskills' type."""
        skill = Skill(
            name="test-agent",
            content="Agent instructions",
            trigger=None,
            is_agentskills_format=True,
        )
        assert skill.get_skill_type() == "agentskills"

    def test_agent_skill_type_with_trigger(self):
        """Test that AgentSkills format takes precedence over trigger type."""
        skill = Skill(
            name="test-agent",
            content="Agent instructions",
            trigger=KeywordTrigger(keywords=["test"]),
            is_agentskills_format=True,
        )
        # AgentSkills format should return 'agentskills' even with triggers
        assert skill.get_skill_type() == "agentskills"


class TestSkillGetTriggers:
    """Tests for Skill.get_triggers() method."""

    def test_no_triggers(self):
        """Test that a skill with trigger=None returns empty list."""
        skill = Skill(
            name="test-repo",
            content="Repository instructions",
            trigger=None,
        )
        assert skill.get_triggers() == []

    def test_keyword_triggers(self):
        """Test that KeywordTrigger returns its keywords."""
        skill = Skill(
            name="test-knowledge",
            content="Knowledge instructions",
            trigger=KeywordTrigger(keywords=["python", "testing", "pytest"]),
        )
        assert skill.get_triggers() == ["python", "testing", "pytest"]

    def test_task_triggers(self):
        """Test that TaskTrigger returns its triggers."""
        skill = Skill(
            name="test-task",
            content="Task instructions",
            trigger=TaskTrigger(triggers=["/deploy", "/build"]),
        )
        assert skill.get_triggers() == ["/deploy", "/build"]

    def test_empty_keyword_triggers(self):
        """Test KeywordTrigger with empty keywords list."""
        skill = Skill(
            name="test-empty",
            content="Instructions",
            trigger=KeywordTrigger(keywords=[]),
        )
        assert skill.get_triggers() == []


class TestSkillToSkillInfo:
    """Tests for Skill.to_skill_info() method."""

    def test_repo_skill_to_info(self):
        """Test conversion of repo skill to SkillInfo."""
        skill = Skill(
            name="test-repo",
            content="Repository instructions",
            source="/path/to/skill.md",
            description="A test repository skill",
            trigger=None,
        )
        info = skill.to_skill_info()

        assert isinstance(info, SkillInfo)
        assert info.name == "test-repo"
        assert info.type == "repo"
        assert info.content == "Repository instructions"
        assert info.triggers == []
        assert info.source == "/path/to/skill.md"
        assert info.description == "A test repository skill"
        assert info.is_agentskills_format is False

    def test_knowledge_skill_to_info(self):
        """Test conversion of knowledge skill to SkillInfo."""
        skill = Skill(
            name="test-knowledge",
            content="Knowledge instructions",
            source="/path/to/knowledge.md",
            trigger=KeywordTrigger(keywords=["python", "coding"]),
        )
        info = skill.to_skill_info()

        assert isinstance(info, SkillInfo)
        assert info.name == "test-knowledge"
        assert info.type == "knowledge"
        assert info.content == "Knowledge instructions"
        assert info.triggers == ["python", "coding"]
        assert info.source == "/path/to/knowledge.md"
        assert info.description is None
        assert info.is_agentskills_format is False

    def test_agent_skill_to_info(self):
        """Test conversion of AgentSkills format skill to SkillInfo."""
        skill = Skill(
            name="pdf-tools",
            content="PDF processing instructions",
            source="/skills/pdf-tools/SKILL.md",
            description="Tools for working with PDF files",
            trigger=None,
            is_agentskills_format=True,
        )
        info = skill.to_skill_info()

        assert isinstance(info, SkillInfo)
        assert info.name == "pdf-tools"
        assert info.type == "agentskills"
        assert info.content == "PDF processing instructions"
        assert info.triggers == []
        assert info.source == "/skills/pdf-tools/SKILL.md"
        assert info.description == "Tools for working with PDF files"
        assert info.is_agentskills_format is True

    def test_task_skill_to_info(self):
        """Test conversion of task skill to SkillInfo."""
        skill = Skill(
            name="deploy-task",
            content="Deployment instructions with ${env}",
            source="/tasks/deploy.md",
            trigger=TaskTrigger(triggers=["/deploy"]),
        )
        info = skill.to_skill_info()

        assert isinstance(info, SkillInfo)
        assert info.name == "deploy-task"
        assert info.type == "knowledge"
        # TaskTrigger appends guidance about variables to the content
        assert "Deployment instructions with ${env}" in info.content
        assert info.triggers == ["/deploy"]
        assert info.source == "/tasks/deploy.md"

    def test_skill_info_with_none_values(self):
        """Test SkillInfo handles None values correctly."""
        skill = Skill(
            name="minimal",
            content="Minimal content",
            trigger=None,
        )
        info = skill.to_skill_info()

        assert info.name == "minimal"
        assert info.type == "repo"
        assert info.content == "Minimal content"
        assert info.triggers == []
        assert info.source is None
        assert info.description is None
        assert info.is_agentskills_format is False


class TestSkillInfoDataclass:
    """Tests for the SkillInfo dataclass itself."""

    def test_skill_info_creation(self):
        """Test direct creation of SkillInfo."""
        info = SkillInfo(
            name="test",
            type="repo",
            content="content",
            triggers=[],
            source=None,
            description=None,
            is_agentskills_format=False,
        )
        assert info.name == "test"
        assert info.type == "repo"

    def test_skill_info_with_all_types(self):
        """Test SkillInfo accepts all valid type values."""
        for skill_type in get_args(SkillType):
            info = SkillInfo(
                name="test",
                type=skill_type,
                content="content",
                triggers=[],
                source=None,
                description=None,
                is_agentskills_format=False,
            )
            assert info.type == skill_type

    def test_skill_info_equality(self):
        """Test SkillInfo equality comparison."""
        info1 = SkillInfo(
            name="test",
            type="repo",
            content="content",
            triggers=["a", "b"],
            source="/path",
            description="desc",
            is_agentskills_format=True,
        )
        info2 = SkillInfo(
            name="test",
            type="repo",
            content="content",
            triggers=["a", "b"],
            source="/path",
            description="desc",
            is_agentskills_format=True,
        )
        assert info1 == info2

    def test_skill_info_inequality(self):
        """Test SkillInfo inequality comparison."""
        info1 = SkillInfo(
            name="test1",
            type="repo",
            content="content",
            triggers=[],
            source=None,
            description=None,
            is_agentskills_format=False,
        )
        info2 = SkillInfo(
            name="test2",
            type="repo",
            content="content",
            triggers=[],
            source=None,
            description=None,
            is_agentskills_format=False,
        )
        assert info1 != info2
