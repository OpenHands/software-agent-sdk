from pathlib import Path

import pytest
from pydantic import ValidationError

from openhands.sdk.subagent.schema import (
    _CONDENSER_VALID_KEYS,
    AgentDefinition,
    _extract_examples,
)


class TestAgentDefinition:
    """Tests for AgentDefinition loading."""

    def test_load_agent_basic(self, tmp_path: Path):
        """Test loading a basic agent definition."""
        agent_md = tmp_path / "test-agent.md"
        agent_md.write_text(
            """---
name: test-agent
description: A test agent
model: gpt-4
tools:
  - Read
  - Write
---

You are a test agent.
"""
        )

        agent = AgentDefinition.load(agent_md)

        assert agent.name == "test-agent"
        assert agent.description == "A test agent"
        assert agent.model == "gpt-4"
        assert agent.tools == ["Read", "Write"]
        assert agent.system_prompt == "You are a test agent."

    def test_load_agent_with_examples(self, tmp_path: Path):
        """Test loading agent with when_to_use examples."""
        agent_md = tmp_path / "helper.md"
        agent_md.write_text(
            """---
name: helper
description: A helper. <example>When user needs help</example>
---

Help the user.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert len(agent.when_to_use_examples) == 1
        assert "When user needs help" in agent.when_to_use_examples[0]

    def test_load_agent_with_color(self, tmp_path: Path):
        """Test loading agent with color."""
        agent_md = tmp_path / "colored.md"
        agent_md.write_text(
            """---
name: colored
color: blue
---

Content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.color == "blue"

    def test_load_agent_with_tools_as_string(self, tmp_path: Path):
        """Test loading agent with tools as single string."""
        agent_md = tmp_path / "single-tool.md"
        agent_md.write_text(
            """---
name: single-tool
tools: Read
---

Content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.tools == ["Read"]

    def test_load_agent_defaults(self, tmp_path: Path):
        """Test agent defaults when fields not provided."""
        agent_md = tmp_path / "minimal.md"
        agent_md.write_text(
            """---
---

Just content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.name == "minimal"  # From filename
        assert agent.model == "inherit"
        assert agent.tools == []

    def test_load_agent_with_max_iteration_per_run(self, tmp_path: Path):
        """Test loading agent with max_iteration_per_run."""
        agent_md = tmp_path / "limited.md"
        agent_md.write_text(
            """---
name: limited
max_iteration_per_run: 10
---

Content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.max_iteration_per_run == 10

    def test_load_agent_without_max_iteration_per_run(self, tmp_path: Path):
        """Test that max_iteration_per_run defaults to None when omitted."""
        agent_md = tmp_path / "default.md"
        agent_md.write_text(
            """---
name: default-iter
---

Content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.max_iteration_per_run is None

    def test_max_iteration_per_run_not_in_metadata(self, tmp_path: Path):
        """Test that max_iteration_per_run doesn't leak into metadata."""
        agent_md = tmp_path / "meta-check.md"
        agent_md.write_text(
            """---
name: meta-check
max_iteration_per_run: 5
custom_field: value
---

Content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert "max_iteration_per_run" not in agent.metadata
        assert agent.metadata.get("custom_field") == "value"

    def test_max_iteration_per_run_zero_raises(self):
        """max_iteration_per_run=0 should fail Pydantic validation."""
        with pytest.raises(ValidationError):
            AgentDefinition(name="bad", max_iteration_per_run=0)

    def test_max_iteration_per_run_negative_raises(self):
        """Negative max_iteration_per_run should fail Pydantic validation."""
        with pytest.raises(ValidationError):
            AgentDefinition(name="bad", max_iteration_per_run=-1)

    def test_load_agent_with_metadata(self, tmp_path: Path):
        """Test loading agent with extra metadata."""
        agent_md = tmp_path / "meta.md"
        agent_md.write_text(
            """---
name: meta-agent
custom_field: custom_value
---

Content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.metadata.get("custom_field") == "custom_value"

    def test_skills_default_empty(self):
        """Test that skills defaults to empty list."""
        agent = AgentDefinition(name="no-skills")
        assert agent.skills == []

    def test_skills_as_list(self):
        """Test creating AgentDefinition with skill names as list."""
        agent = AgentDefinition(
            name="skilled-agent",
            skills=["code-review", "linting"],
        )
        assert agent.skills == ["code-review", "linting"]

    def test_load_skills_comma_separated(self, tmp_path: Path):
        """Test loading skills from comma-separated frontmatter string."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: skilled-agent
skills: code-review, linting, testing
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert agent.skills == ["code-review", "linting", "testing"]

    def test_load_skills_as_yaml_list(self, tmp_path: Path):
        """Test loading skills from YAML list in frontmatter."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: skilled-agent
skills:
  - code-review
  - linting
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert agent.skills == ["code-review", "linting"]

    def test_load_skills_single_string(self, tmp_path: Path):
        """Test loading a single skill name from frontmatter string."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: skilled-agent
skills: code-review
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert agent.skills == ["code-review"]

    def test_load_skills_default_empty(self, tmp_path: Path):
        """Test that loading from file without skills gives empty list."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: file-agent
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert agent.skills == []

    def test_load_skills_not_in_metadata(self, tmp_path: Path):
        """Test that skills field is excluded from extra metadata."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: agent
skills: my-skill
custom_field: value
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert "skills" not in agent.metadata
        assert agent.metadata.get("custom_field") == "value"

    def test_load_agent_with_profile_store_dir(self, tmp_path: Path):
        """Test loading agent with profile_store_dir from frontmatter."""
        agent_md = tmp_path / "profiled.md"
        agent_md.write_text(
            """---
name: profiled
profile_store_dir: /custom/profiles
---

Content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.profile_store_dir == "/custom/profiles"

    def test_load_agent_without_profile_store_dir(self, tmp_path: Path):
        """Test that profile_store_dir defaults to None when omitted."""
        agent_md = tmp_path / "default.md"
        agent_md.write_text(
            """---
name: no-profile-dir
---

Content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.profile_store_dir is None

    def test_profile_store_dir_not_in_metadata(self, tmp_path: Path):
        """Test that profile_store_dir doesn't leak into metadata."""
        agent_md = tmp_path / "meta-check.md"
        agent_md.write_text(
            """---
name: meta-check
profile_store_dir: /some/path
custom_field: value
---

Content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert "profile_store_dir" not in agent.metadata
        assert agent.metadata.get("custom_field") == "value"

    def test_profile_store_dir_default_none(self):
        """Test that profile_store_dir defaults to None on direct construction."""
        agent = AgentDefinition(name="test")
        assert agent.profile_store_dir is None

    def test_condenser_default_none(self):
        """Test that condenser defaults to None on direct construction."""
        agent = AgentDefinition(name="test")
        assert agent.condenser is None

    def test_condenser_as_dict(self):
        """Test creating AgentDefinition with condenser as dict."""
        config = {"max_size": 100, "keep_first": 2}
        agent = AgentDefinition(name="condensed-agent", condenser=config)
        assert agent.condenser == config

    def test_load_condenser_from_frontmatter(self, tmp_path: Path):
        """Test loading condenser from YAML frontmatter."""
        agent_md = tmp_path / "condensed.md"
        agent_md.write_text(
            """---
name: condensed-agent
condenser:
  max_size: 100
  keep_first: 3
---

You are an agent with condensation.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.condenser is not None
        assert agent.condenser["max_size"] == 100
        assert agent.condenser["keep_first"] == 3

    def test_load_condenser_not_in_metadata(self, tmp_path: Path):
        """Test that condenser doesn't leak into metadata."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: agent
condenser:
  max_size: 50
custom_field: value
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert "condenser" not in agent.metadata
        assert agent.metadata.get("custom_field") == "value"

    def test_load_condenser_non_dict_raises(self, tmp_path: Path):
        """Test that non-dict condenser value raises ValueError."""
        agent_md = tmp_path / "bad-condenser.md"
        agent_md.write_text(
            """---
name: bad-condenser
condenser: true
---

Prompt.
"""
        )
        with pytest.raises(ValueError, match="must be a mapping"):
            AgentDefinition.load(agent_md)

    def test_load_condenser_unknown_key_raises(self, tmp_path: Path):
        """Test that unknown condenser parameters raise ValueError."""
        agent_md = tmp_path / "bad-condenser.md"
        agent_md.write_text(
            """---
name: bad-condenser
condenser:
  max_size: 100
  bogus_param: 42
---

Prompt.
"""
        )
        with pytest.raises(ValueError, match="Unknown condenser parameter"):
            AgentDefinition.load(agent_md)

    def test_load_without_condenser(self, tmp_path: Path):
        """Test that loading from file without condenser gives None."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: no-condenser
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert agent.condenser is None

    def test_condenser_valid_keys_match_llm_summarizing_condenser(self):
        """Ensure _CONDENSER_VALID_KEYS stays in sync with LLMSummarizingCondenser."""
        from openhands.sdk.context.condenser import LLMSummarizingCondenser

        # Get all user-configurable fields (exclude 'llm' which is inherited)
        condenser_fields = set(LLMSummarizingCondenser.model_fields.keys()) - {"llm"}
        assert _CONDENSER_VALID_KEYS == condenser_fields, (
            f"_CONDENSER_VALID_KEYS is out of sync with LLMSummarizingCondenser. "
            f"Missing: {condenser_fields - _CONDENSER_VALID_KEYS}, "
            f"Extra: {_CONDENSER_VALID_KEYS - condenser_fields}"
        )


class TestExtractExamples:
    """Tests for _extract_examples function."""

    def test_extract_single_example(self):
        """Test extracting single example."""
        description = "A tool. <example>Use when X</example>"
        examples = _extract_examples(description)
        assert examples == ["Use when X"]

    def test_extract_multiple_examples(self):
        """Test extracting multiple examples."""
        description = "<example>First</example> text <example>Second</example>"
        examples = _extract_examples(description)
        assert examples == ["First", "Second"]

    def test_extract_no_examples(self):
        """Test when no examples present."""
        description = "A tool without examples"
        examples = _extract_examples(description)
        assert examples == []

    def test_extract_multiline_example(self):
        """Test extracting multiline example."""
        description = """<example>
        Multi
        Line
        </example>"""
        examples = _extract_examples(description)
        assert len(examples) == 1
        assert "Multi" in examples[0]
