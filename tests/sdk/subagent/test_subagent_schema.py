from pathlib import Path

import pytest
from pydantic import ValidationError

from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.subagent.schema import (
    AgentDefinition,
    _extract_examples,
    _resolve_env_vars,
    _resolve_env_vars_deep,
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

    def test_load_agent_with_hooks(self, tmp_path: Path):
        """Test loading agent with hook configuration."""
        agent_md = tmp_path / "hooked.md"
        agent_md.write_text(
            """---
name: hooked-agent
description: An agent with hooks
hooks:
  pre_tool_use:
    - matcher: "terminal"
      hooks:
        - command: "./scripts/validate.sh"
          timeout: 10
  post_tool_use:
    - matcher: "*"
      hooks:
        - command: "./scripts/log.sh"
---

You are a hooked agent.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.hooks is not None
        assert isinstance(agent.hooks, HookConfig)
        assert len(agent.hooks.pre_tool_use) == 1
        assert agent.hooks.pre_tool_use[0].matcher == "terminal"
        assert agent.hooks.pre_tool_use[0].hooks[0].command == "./scripts/validate.sh"
        assert agent.hooks.pre_tool_use[0].hooks[0].timeout == 10
        assert len(agent.hooks.post_tool_use) == 1
        assert agent.hooks.post_tool_use[0].matcher == "*"
        # hooks should not appear in metadata
        assert "hooks" not in agent.metadata

    def test_load_agent_hooks_none_when_missing(self, tmp_path: Path):
        """Test that hooks defaults to None when not in frontmatter."""
        agent_md = tmp_path / "no-hooks.md"
        agent_md.write_text(
            """---
name: no-hooks-agent
---

Content.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.hooks is None

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

    def test_mcp_servers_default_none(self):
        """Test that mcp_servers defaults to None on direct construction."""
        agent = AgentDefinition(name="test")
        assert agent.mcp_servers is None

    def test_mcp_servers_as_dict(self):
        """Test creating AgentDefinition with mcp_servers as dict."""
        servers = {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
        agent = AgentDefinition(name="mcp-agent", mcp_servers=servers)
        assert agent.mcp_servers == servers

    def test_load_mcp_servers_from_frontmatter(self, tmp_path: Path):
        """Test loading mcp_servers from YAML frontmatter."""
        agent_md = tmp_path / "mcp-agent.md"
        agent_md.write_text(
            """---
name: mcp-agent
mcp_servers:
  fetch:
    command: uvx
    args:
      - mcp-server-fetch
  filesystem:
    command: npx
    args:
      - -y
      - "@modelcontextprotocol/server-filesystem"
---

You are an agent with MCP tools.
"""
        )

        agent = AgentDefinition.load(agent_md)
        assert agent.mcp_servers is not None
        assert "fetch" in agent.mcp_servers
        assert agent.mcp_servers["fetch"]["command"] == "uvx"
        assert agent.mcp_servers["fetch"]["args"] == ["mcp-server-fetch"]
        assert "filesystem" in agent.mcp_servers

    def test_load_mcp_servers_not_in_metadata(self, tmp_path: Path):
        """Test that mcp_servers doesn't leak into metadata."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: agent
mcp_servers:
  fetch:
    command: uvx
    args:
      - mcp-server-fetch
custom_field: value
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert "mcp_servers" not in agent.metadata
        assert agent.metadata.get("custom_field") == "value"

    def test_load_without_mcp_servers(self, tmp_path: Path):
        """Test that loading from file without mcp_servers gives None."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: no-mcp
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert agent.mcp_servers is None

    def test_mcp_servers_env_vars_resolved_in_env_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that ${VAR} references in env values are resolved."""
        monkeypatch.setenv("MY_API_KEY", "secret-123")
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: agent
mcp_servers:
  my-server:
    command: npx
    args:
      - mcp-server
    env:
      API_KEY: ${MY_API_KEY}
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        mcp_servers = agent.mcp_servers
        assert mcp_servers is not None
        assert mcp_servers["my-server"]["env"]["API_KEY"] == "secret-123"

    def test_mcp_servers_env_vars_resolved_in_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that ${VAR} references in command are resolved."""
        monkeypatch.setenv("PLUGIN_ROOT", "/opt/plugins")
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: agent
mcp_servers:
  my-server:
    command: ${PLUGIN_ROOT}/bin/server
    args:
      - --config
      - ${PLUGIN_ROOT}/config.json
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        mcp_servers = agent.mcp_servers
        assert mcp_servers is not None
        assert mcp_servers["my-server"]["command"] == "/opt/plugins/bin/server"
        assert mcp_servers["my-server"]["args"] == [
            "--config",
            "/opt/plugins/config.json",
        ]

    def test_mcp_servers_env_vars_resolved_in_url_and_headers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that ${VAR} references in url and headers are resolved."""
        monkeypatch.setenv("API_BASE", "https://api.example.com")
        monkeypatch.setenv("AUTH_TOKEN", "tok-abc")
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: agent
mcp_servers:
  remote:
    type: http
    url: ${API_BASE}/mcp
    headers:
      Authorization: Bearer ${AUTH_TOKEN}
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        mcp_servers = agent.mcp_servers
        assert mcp_servers is not None
        assert mcp_servers["remote"]["url"] == "https://api.example.com/mcp"
        assert mcp_servers["remote"]["headers"]["Authorization"] == "Bearer tok-abc"

    def test_mcp_servers_unset_env_var_kept_as_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that unset ${VAR} references are left unchanged."""
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: agent
mcp_servers:
  my-server:
    command: ${NONEXISTENT_VAR}
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        mcp_servers = agent.mcp_servers
        assert mcp_servers is not None
        assert mcp_servers["my-server"]["command"] == "${NONEXISTENT_VAR}"

    def test_permission_mode_defaults_to_none(self):
        """Test that permission_mode defaults to None (inherit parent)."""
        agent = AgentDefinition(name="test")
        assert agent.permission_mode is None

    @pytest.mark.parametrize(
        "mode",
        [
            "never_confirm",
            "confirm_risky",
            "always_confirm",
        ],
    )
    def test_permission_mode_valid_values(self, mode: str):
        """Test setting permission_mode to each valid value."""
        agent = AgentDefinition(name="test", permission_mode=mode)
        assert agent.permission_mode == mode

    def test_load_permission_mode_from_frontmatter(self, tmp_path: Path):
        """Test loading permission_mode from frontmatter."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: secure-agent
permission_mode: always_confirm
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert agent.permission_mode == "always_confirm"

    def test_load_permission_mode_none_when_omitted(self, tmp_path: Path):
        """Test that permission_mode is None when not in frontmatter."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: basic-agent
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert agent.permission_mode is None

    def test_load_permission_mode_not_in_metadata(self, tmp_path: Path):
        """Test that permission_mode is excluded from extra metadata."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: agent
permission_mode: never_confirm
custom_field: value
---

Prompt.
"""
        )
        agent = AgentDefinition.load(agent_md)
        assert "permission_mode" not in agent.metadata
        assert agent.metadata.get("custom_field") == "value"

    def test_get_confirmation_policy_none(self):
        """Test that None permission_mode returns None (inherit parent)."""
        agent = AgentDefinition(name="test")
        assert agent.get_confirmation_policy() is None

    @pytest.mark.parametrize(
        "permission_mode, expected_class_name",
        [
            ("always_confirm", "AlwaysConfirm"),
            ("never_confirm", "NeverConfirm"),
            ("confirm_risky", "ConfirmRisky"),
        ],
    )
    def test_get_confirmation_policy_returns_instance(
        self, permission_mode: str, expected_class_name: str
    ):
        """Test that each permission_mode returns the correct policy instance."""
        agent = AgentDefinition(name="test", permission_mode=permission_mode)
        policy = agent.get_confirmation_policy()
        assert policy is not None
        assert type(policy).__name__ == expected_class_name

    def test_load_permission_mode_invalid_raises(self, tmp_path: Path):
        """Test that an invalid permission_mode raises ValueError."""
        agent_md = tmp_path / "agent.md"
        agent_md.write_text(
            """---
name: agent
permission_mode: invalid_mode
---

Prompt.
"""
        )
        with pytest.raises(ValueError, match="Invalid permission_mode"):
            AgentDefinition.load(agent_md)


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


@pytest.mark.parametrize(
    ("input_val", "env_vars", "expected"),
    [
        ("${FOO}", {"FOO": "bar"}, "bar"),
        (
            "${HOST}:${PORT}",
            {"HOST": "localhost", "PORT": "8080"},
            "localhost:8080",
        ),
        ("prefix_${VAR}_suffix", {"VAR": "mid"}, "prefix_mid_suffix"),
        ("plain text", {}, "plain text"),
        ("${MISSING}", {}, "${MISSING}"),
        ("$FOO", {"FOO": "bar"}, "bar"),
        ("$FOO/path", {"FOO": "/root"}, "/root/path"),
    ],
    ids=[
        "single_var",
        "multiple_vars",
        "var_embedded_in_text",
        "no_vars",
        "unset_var_unchanged",
        "dollar_without_braces",
        "dollar_without_braces_in_path",
    ],
)
def test_resolve_env_vars(
    monkeypatch: pytest.MonkeyPatch,
    input_val: str,
    env_vars: dict[str, str],
    expected: str,
):
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)
    assert _resolve_env_vars(input_val) == expected


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        (42, 42),
        (None, None),
        (True, True),
        (3.14, 3.14),
    ],
    ids=["int", "none", "bool", "float"],
)
def test_resolve_env_vars_deep_non_string_passthrough(
    input_val: object, expected: object
):
    assert _resolve_env_vars_deep(input_val) is expected


def test_resolve_env_vars_deep_string(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VAL", "resolved")
    assert _resolve_env_vars_deep("${VAL}") == "resolved"


def test_resolve_env_vars_deep_dict(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("A", "1")
    monkeypatch.setenv("B", "2")
    result = _resolve_env_vars_deep({"key_a": "${A}", "key_b": "${B}"})
    assert result == {"key_a": "1", "key_b": "2"}


def test_resolve_env_vars_deep_list(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("X", "hello")
    result = _resolve_env_vars_deep(["${X}", "literal", "${X}"])
    assert result == ["hello", "literal", "hello"]


def test_resolve_env_vars_deep_nested(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CMD", "/usr/bin/server")
    monkeypatch.setenv("TOKEN", "secret")
    data = {
        "command": "${CMD}",
        "args": ["--token", "${TOKEN}"],
        "env": {"API_TOKEN": "${TOKEN}"},
        "port": 8080,
    }
    result = _resolve_env_vars_deep(data)
    assert result["command"] == "/usr/bin/server"
    assert result["args"] == ["--token", "secret"]
    assert result["env"]["API_TOKEN"] == "secret"
    assert result["port"] == 8080  # non-string left untouched
