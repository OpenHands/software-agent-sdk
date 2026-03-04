from pathlib import Path

import pytest
from pydantic import ValidationError

from openhands.sdk.subagent.schema import AgentDefinition, _extract_examples


# ── Helpers ──────────────────────────────────────────────────────────


def _write_agent_md(tmp_path: Path, frontmatter: str, body: str = "Prompt.") -> Path:
    """Write a minimal agent .md file and return its path."""
    md = tmp_path / "agent.md"
    md.write_text(f"---\n{frontmatter}---\n\n{body}\n")
    return md


def _load(tmp_path: Path, frontmatter: str, body: str = "Prompt.") -> AgentDefinition:
    """Shortcut: write + load an agent definition."""
    return AgentDefinition.load(_write_agent_md(tmp_path, frontmatter, body))


# ── AgentDefinition.load ────────────────────────────────────────────


def test_load_basic(tmp_path: Path):
    agent = _load(
        tmp_path,
        "name: test-agent\ndescription: A test agent\nmodel: gpt-4\n"
        "tools:\n  - Read\n  - Write\n",
        body="You are a test agent.",
    )
    assert agent.name == "test-agent"
    assert agent.description == "A test agent"
    assert agent.model == "gpt-4"
    assert agent.tools == ["Read", "Write"]
    assert agent.system_prompt == "You are a test agent."


def test_load_examples(tmp_path: Path):
    agent = _load(
        tmp_path,
        "name: helper\ndescription: A helper. <example>When user needs help</example>\n",  # noqa
        body="Help the user.",
    )
    assert agent.when_to_use_examples == ["When user needs help"]


def test_load_color(tmp_path: Path):
    assert _load(tmp_path, "name: c\ncolor: blue\n").color == "blue"


def test_load_tools_as_string(tmp_path: Path):
    assert _load(tmp_path, "name: t\ntools: Read\n").tools == ["Read"]


def test_load_defaults(tmp_path: Path):
    agent = _load(tmp_path, "", body="Just content.")
    assert agent.name == "agent"  # from filename
    assert agent.model == "inherit"
    assert agent.tools == []
    assert agent.working_dir is None
    assert agent.max_iteration_per_run is None


def test_load_max_iteration_per_run(tmp_path: Path):
    assert (
        _load(tmp_path, "name: x\nmax_iteration_per_run: 10\n").max_iteration_per_run
        == 10
    )


def test_load_metadata(tmp_path: Path):
    agent = _load(tmp_path, "name: m\ncustom_field: custom_value\n")
    assert agent.metadata["custom_field"] == "custom_value"


def test_load_metadata_excludes_known_fields(tmp_path: Path):
    """
    Known fields (max_iteration_per_run, skills, working_dir)
    don't leak into metadata.
    """
    agent = _load(
        tmp_path,
        "name: m\nmax_iteration_per_run: 5\nskills: my-skill\n"
        "working_dir: /some/path\ncustom_field: value\n",
    )
    assert "max_iteration_per_run" not in agent.metadata
    assert "skills" not in agent.metadata
    assert "working_dir" not in agent.metadata
    assert agent.metadata["custom_field"] == "value"


# ── Skills loading ──────────────────────────────────────────────────


def test_load_skills_comma_separated(tmp_path: Path):
    assert _load(tmp_path, "name: s\nskills: a, b, c\n").skills == ["a", "b", "c"]


def test_load_skills_yaml_list(tmp_path: Path):
    assert _load(tmp_path, "name: s\nskills:\n  - a\n  - b\n").skills == ["a", "b"]


def test_load_skills_single_string(tmp_path: Path):
    assert _load(tmp_path, "name: s\nskills: code-review\n").skills == ["code-review"]


def test_load_skills_default_empty(tmp_path: Path):
    assert _load(tmp_path, "name: s\n").skills == []


# ── Working dir loading ─────────────────────────────────────────────


def test_load_working_dir_absolute(tmp_path: Path):
    assert (
        _load(tmp_path, "name: w\nworking_dir: /tmp/sandbox\n").working_dir
        == "/tmp/sandbox"
    )


def test_load_working_dir_relative(tmp_path: Path):
    assert (
        _load(tmp_path, "name: w\nworking_dir: frontend/\n").working_dir == "frontend/"
    )


def test_load_working_dir_default_none(tmp_path: Path):
    assert _load(tmp_path, "name: w\n").working_dir is None


# ── Pydantic field defaults & validation ────────────────────────────


def test_skills_default_empty():
    assert AgentDefinition(name="x").skills == []


def test_skills_as_list():
    assert AgentDefinition(name="x", skills=["a", "b"]).skills == ["a", "b"]


def test_working_dir_default_none():
    assert AgentDefinition(name="x").working_dir is None


def test_working_dir_set():
    assert AgentDefinition(name="x", working_dir="/tmp").working_dir == "/tmp"


def test_max_iteration_per_run_zero_raises():
    with pytest.raises(ValidationError):
        AgentDefinition(name="bad", max_iteration_per_run=0)


def test_max_iteration_per_run_negative_raises():
    with pytest.raises(ValidationError):
        AgentDefinition(name="bad", max_iteration_per_run=-1)


# ── from_factory_func ───────────────────────────────────────────────


def test_from_factory_func_basic():
    from openhands.sdk import Agent, AgentContext
    from openhands.sdk.tool.spec import Tool

    def factory(llm):
        return Agent(
            llm=llm,
            tools=[Tool(name="terminal")],
            agent_context=AgentContext(system_message_suffix="You are helpful."),
        )

    defn = AgentDefinition.from_factory_func("helper", factory, "A helper agent")
    assert defn.name == "helper"
    assert defn.description == "A helper agent"
    assert defn.tools == ["terminal"]
    assert defn.system_prompt == "You are helpful."
    assert defn.model == "__introspect__"


def test_from_factory_func_extracts_skills():
    from openhands.sdk import Agent, AgentContext
    from openhands.sdk.context.skills.skill import Skill

    skill = Skill(name="my-skill", content="Skill content")

    def factory(llm):
        return Agent(llm=llm, agent_context=AgentContext(skills=[skill]))

    defn = AgentDefinition.from_factory_func("skilled", factory, "Has skills")
    assert defn.skills == ["my-skill"]


def test_from_factory_func_no_context():
    from openhands.sdk import Agent

    defn = AgentDefinition.from_factory_func("bare", lambda llm: Agent(llm=llm), "Bare")
    assert defn.system_prompt == ""
    assert defn.skills == []


def test_from_factory_func_model_override():
    from openhands.sdk import Agent

    def factory(llm):
        return Agent(llm=llm.model_copy(update={"model": "gpt-4o"}))

    defn = AgentDefinition.from_factory_func("custom", factory, "Custom model")
    assert defn.model == "gpt-4o"


# ── _extract_examples ───────────────────────────────────────────────


def test_extract_single_example():
    assert _extract_examples("A tool. <example>Use when X</example>") == ["Use when X"]


def test_extract_multiple_examples():
    assert _extract_examples(
        "<example>First</example> text <example>Second</example>"
    ) == [
        "First",
        "Second",
    ]


def test_extract_no_examples():
    assert _extract_examples("A tool without examples") == []


def test_extract_multiline_example():
    examples = _extract_examples("<example>\n  Multi\n  Line\n  </example>")
    assert len(examples) == 1
    assert "Multi" in examples[0]
