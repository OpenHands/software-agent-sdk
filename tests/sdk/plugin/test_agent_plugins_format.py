"""Tests for the Agent Plugins format: detection, manifest, and components."""

import json
from pathlib import Path

import pytest

from openhands.sdk.plugin import (
    AgentPluginsFormat,
    ClaudeCodePluginFormat,
    PluginManifest,
    detect_format,
)
from openhands.sdk.plugin.format.agent_plugins import (
    _MANIFEST_SCHEMA_FILE,
    EXTENSION_NAMESPACE,
    MANIFEST_SCHEMA_URL,
    _load_schema,
)


SCHEMA_1_0_0 = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

# The official example plugin's manifest, verbatim.
# https://github.com/agentplugins/agent-plugins-example/blob/main/plugin.json
EXAMPLE_MANIFEST = {
    "$schema": SCHEMA_1_0_0,
    "name": "agent-plugins-example",
    "version": "1.0.0",
    "description": (
        "A copyable reference package and migration guide for Agent Plugins v1.0.0."
    ),
    "license": "MIT",
    "keywords": ["agent-plugins", "example", "migration", "skills"],
}


def write_manifest(plugin_dir: Path, manifest: dict | str) -> Path:
    """Write a root-level plugin.json, creating the plugin dir."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path = plugin_dir / "plugin.json"
    path.write_text(
        manifest if isinstance(manifest, str) else json.dumps(manifest),
        encoding="utf-8",
    )
    return path


def write_extension_components(root: Path) -> None:
    """Write one hook, agent and command under ``root``, in the shared layout."""
    (root / "hooks").mkdir(parents=True, exist_ok=True)
    (root / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": "echo hi"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "agents").mkdir(parents=True, exist_ok=True)
    (root / "agents" / "helper.md").write_text(
        "---\nname: helper\ndescription: A helper agent.\n---\n\nHelp out.\n",
        encoding="utf-8",
    )
    (root / "commands").mkdir(parents=True, exist_ok=True)
    (root / "commands" / "now.md").write_text(
        "---\ndescription: Report the weather now.\n---\n\nReport it.\n",
        encoding="utf-8",
    )


class TestDetect:
    """Detection is presence-only: a root plugin.json claims the directory."""

    def test_detects_root_manifest(self, tmp_path: Path):
        write_manifest(tmp_path / "p", EXAMPLE_MANIFEST)
        assert AgentPluginsFormat.detect(tmp_path / "p") is True

    def test_ignores_nested_claude_code_manifest(self, tmp_path: Path):
        plugin_dir = tmp_path / "p"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text('{"name": "p"}')

        assert AgentPluginsFormat.detect(plugin_dir) is False

    def test_ignores_bare_dir(self, tmp_path: Path):
        (tmp_path / "p").mkdir()
        assert AgentPluginsFormat.detect(tmp_path / "p") is False

    def test_claims_malformed_manifest(self, tmp_path: Path):
        """Claimed, not fallen through: load_manifest() rejects it loudly."""
        write_manifest(tmp_path / "p", "{not json")

        assert AgentPluginsFormat.detect(tmp_path / "p") is True

    def test_ignores_manifest_directory(self, tmp_path: Path):
        """A plugin.json *directory* is not a manifest."""
        (tmp_path / "p" / "plugin.json").mkdir(parents=True)
        assert AgentPluginsFormat.detect(tmp_path / "p") is False

    def test_ignores_missing_dir(self, tmp_path: Path):
        assert AgentPluginsFormat.detect(tmp_path / "does-not-exist") is False

    def test_not_registered_yet(self, tmp_path: Path):
        """Pins the decision to keep this format out of ``_FORMATS``."""
        write_manifest(tmp_path / "p", EXAMPLE_MANIFEST)

        assert isinstance(detect_format(tmp_path / "p"), ClaudeCodePluginFormat)


class TestLoadManifestValid:
    """Manifests the closed schema accepts."""

    def test_loads_example_plugin(self, tmp_path: Path):
        write_manifest(tmp_path, EXAMPLE_MANIFEST)

        manifest = AgentPluginsFormat().load_manifest(tmp_path)

        assert isinstance(manifest, PluginManifest)
        assert manifest.name == "agent-plugins-example"
        assert manifest.version == "1.0.0"
        assert manifest.description.startswith("A copyable reference package")

    def test_preserves_agent_plugins_only_fields(self, tmp_path: Path):
        """Fields with no PluginManifest column survive via extra='allow'."""
        write_manifest(
            tmp_path,
            {
                **EXAMPLE_MANIFEST,
                "homepage": "https://example.com",
                "repository": "https://github.com/example/plugin",
                "extensions": {"com.example.client": {"entry_command": "now"}},
            },
        )

        dumped = AgentPluginsFormat().load_manifest(tmp_path).model_dump()

        assert dumped["$schema"] == SCHEMA_1_0_0
        assert dumped["license"] == "MIT"
        assert dumped["keywords"] == ["agent-plugins", "example", "migration", "skills"]
        assert dumped["homepage"] == "https://example.com"
        assert dumped["repository"] == "https://github.com/example/plugin"
        assert dumped["extensions"] == {"com.example.client": {"entry_command": "now"}}

    def test_minimal_manifest_uses_model_defaults(self, tmp_path: Path):
        """Only $schema and name are required by the schema."""
        write_manifest(tmp_path, {"$schema": SCHEMA_1_0_0, "name": "minimal"})

        manifest = AgentPluginsFormat().load_manifest(tmp_path)

        assert manifest.name == "minimal"
        assert manifest.version == "1.0.0"
        assert manifest.description == ""
        assert manifest.author is None

    def test_author_object(self, tmp_path: Path):
        write_manifest(
            tmp_path,
            {
                "$schema": SCHEMA_1_0_0,
                "name": "authored",
                "author": {"name": "Ada", "email": "ada@example.com"},
            },
        )

        manifest = AgentPluginsFormat().load_manifest(tmp_path)

        assert manifest.author is not None
        assert manifest.author.name == "Ada"
        assert manifest.author.email == "ada@example.com"

    @pytest.mark.parametrize(
        "author", [{}, {"email": "ada@example.com"}, {"url": "https://example.com"}]
    )
    def test_author_without_name(self, tmp_path: Path, author: dict):
        """The schema does not require author.name, so neither may we."""
        write_manifest(
            tmp_path, {"$schema": SCHEMA_1_0_0, "name": "p", **{"author": author}}
        )

        manifest = AgentPluginsFormat().load_manifest(tmp_path)

        assert manifest.author is not None
        assert manifest.author.name == ""

    def test_utf8_bom_is_tolerated(self, tmp_path: Path):
        """RFC 8259 lets a parser ignore a leading BOM."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "plugin.json").write_bytes(
            b"\xef\xbb\xbf" + json.dumps(EXAMPLE_MANIFEST).encode("utf-8")
        )

        assert AgentPluginsFormat().load_manifest(tmp_path).name == (
            "agent-plugins-example"
        )

    @pytest.mark.parametrize(
        "name",
        ["a", "a-b", "a.b", "plugin1", "a" * 64, "a-b.c-d", "0abc"],
    )
    def test_accepts_valid_names(self, tmp_path: Path, name: str):
        write_manifest(tmp_path, {"$schema": SCHEMA_1_0_0, "name": name})

        assert AgentPluginsFormat().load_manifest(tmp_path).name == name


class TestLoadManifestNonFatal:
    """Violations the spec says to report and ignore."""

    def test_unknown_top_level_field_is_dropped(self, tmp_path: Path, caplog):
        write_manifest(
            tmp_path, {**EXAMPLE_MANIFEST, "entry_command": "now", "nonsense": 1}
        )

        manifest = AgentPluginsFormat().load_manifest(tmp_path)

        assert manifest.name == "agent-plugins-example"
        dumped = manifest.model_dump()
        assert "entry_command" not in dumped or dumped["entry_command"] is None
        assert "nonsense" not in dumped
        assert "Ignoring unknown top-level field(s)" in caplog.text
        assert "entry_command" in caplog.text
        assert "nonsense" in caplog.text

    @pytest.mark.parametrize("extensions", [[], "nope", 3, None])
    def test_non_object_extensions_is_dropped(self, tmp_path: Path, caplog, extensions):
        write_manifest(tmp_path, {**EXAMPLE_MANIFEST, "extensions": extensions})

        manifest = AgentPluginsFormat().load_manifest(tmp_path)

        assert manifest.name == "agent-plugins-example"
        assert "extensions" not in manifest.model_dump()
        assert "Ignoring non-object 'extensions'" in caplog.text

    def test_both_non_fatal_violations_together(self, tmp_path: Path, caplog):
        write_manifest(tmp_path, {**EXAMPLE_MANIFEST, "nonsense": 1, "extensions": []})

        manifest = AgentPluginsFormat().load_manifest(tmp_path)

        assert manifest.name == "agent-plugins-example"
        assert "Ignoring unknown top-level field(s)" in caplog.text
        assert "Ignoring non-object 'extensions'" in caplog.text

    def test_dropping_unknown_fields_does_not_mask_a_fatal_one(self, tmp_path: Path):
        """Stripping the non-fatal violation must not rescue an invalid name."""
        write_manifest(
            tmp_path, {"$schema": SCHEMA_1_0_0, "name": "BAD--NAME", "nonsense": 1}
        )

        with pytest.raises(ValueError, match="Invalid Agent Plugins manifest"):
            AgentPluginsFormat().load_manifest(tmp_path)


class TestLoadManifestFatal:
    """Violations that reject the whole plugin."""

    def test_missing_schema(self, tmp_path: Path):
        write_manifest(tmp_path, {"name": "no-schema"})

        with pytest.raises(ValueError, match="Unsupported or missing \\$schema"):
            AgentPluginsFormat().load_manifest(tmp_path)

    def test_unsupported_schema_version(self, tmp_path: Path):
        write_manifest(
            tmp_path,
            {
                "$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json",
                "name": "from-the-future",
            },
        )

        with pytest.raises(ValueError, match="Unsupported or missing \\$schema"):
            AgentPluginsFormat().load_manifest(tmp_path)

    def test_non_string_schema(self, tmp_path: Path):
        write_manifest(tmp_path, {"$schema": 1, "name": "weird"})

        with pytest.raises(ValueError, match="Unsupported or missing \\$schema"):
            AgentPluginsFormat().load_manifest(tmp_path)

    def test_invalid_json(self, tmp_path: Path):
        write_manifest(tmp_path, "{not json")

        with pytest.raises(ValueError, match="Invalid JSON"):
            AgentPluginsFormat().load_manifest(tmp_path)

    def test_undecodable_bytes(self, tmp_path: Path):
        """A decode failure is wrapped, not leaked as UnicodeDecodeError."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "plugin.json").write_bytes(
            b'{"$schema": "' + SCHEMA_1_0_0.encode() + b'", "name": "caf\xe9"}'
        )

        with pytest.raises(ValueError, match="Failed to read manifest"):
            AgentPluginsFormat().load_manifest(tmp_path)

    @pytest.mark.parametrize("payload", ["[]", '"a string"', "42", "null"])
    def test_non_object_root(self, tmp_path: Path, payload: str):
        write_manifest(tmp_path, payload)

        with pytest.raises(ValueError, match="must contain a JSON object"):
            AgentPluginsFormat().load_manifest(tmp_path)

    def test_missing_name(self, tmp_path: Path):
        write_manifest(tmp_path, {"$schema": SCHEMA_1_0_0})

        with pytest.raises(ValueError, match="Invalid Agent Plugins manifest"):
            AgentPluginsFormat().load_manifest(tmp_path)

    @pytest.mark.parametrize(
        "name",
        [
            "",  # too short
            "a" * 65,  # too long
            "Uppercase",
            "-leading-hyphen",
            "trailing-hyphen-",
            ".leading-period",
            "trailing-period.",
            "double--hyphen",
            "double..period",
            "under_score",
            "with space",
        ],
    )
    def test_invalid_names(self, tmp_path: Path, name: str):
        write_manifest(tmp_path, {"$schema": SCHEMA_1_0_0, "name": name})

        with pytest.raises(ValueError, match="Invalid Agent Plugins manifest"):
            AgentPluginsFormat().load_manifest(tmp_path)

    def test_string_author_rejected(self, tmp_path: Path):
        """Agent Plugins authors are objects only, unlike the Claude Code format."""
        write_manifest(
            tmp_path, {"$schema": SCHEMA_1_0_0, "name": "p", "author": "Ada <a@e.com>"}
        )

        with pytest.raises(ValueError, match="Invalid Agent Plugins manifest"):
            AgentPluginsFormat().load_manifest(tmp_path)

    def test_unknown_author_field_rejected(self, tmp_path: Path):
        """The non-fatal rule covers top-level fields only, not nested objects."""
        write_manifest(
            tmp_path,
            {"$schema": SCHEMA_1_0_0, "name": "p", "author": {"handle": "ada"}},
        )

        with pytest.raises(ValueError, match="Invalid Agent Plugins manifest"):
            AgentPluginsFormat().load_manifest(tmp_path)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("version", 1),
            ("description", []),
            ("license", False),
            ("keywords", "not-a-list"),
            ("keywords", [1, 2]),
            ("extensions", {"com.example.client": "not-an-object"}),
        ],
    )
    def test_wrong_types(self, tmp_path: Path, field: str, value):
        write_manifest(tmp_path, {**EXAMPLE_MANIFEST, field: value})

        with pytest.raises(ValueError, match="Invalid Agent Plugins manifest"):
            AgentPluginsFormat().load_manifest(tmp_path)

    def test_missing_manifest_file(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Failed to read manifest"):
            AgentPluginsFormat().load_manifest(tmp_path)

    def test_manifest_is_a_directory(self, tmp_path: Path):
        (tmp_path / "plugin.json").mkdir()

        with pytest.raises(ValueError, match="Failed to read manifest"):
            AgentPluginsFormat().load_manifest(tmp_path)


class TestComponentLoaders:
    """Portable core: skills are shared with the base, mcp.json is a follow-up."""

    def test_load_assembles_plugin_with_skills(self, tmp_path: Path):
        write_manifest(tmp_path, EXAMPLE_MANIFEST)
        skill_dir = tmp_path / "skills" / "summarize"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: summarize\ndescription: Summarize text.\n---\n\nDo it.\n",
            encoding="utf-8",
        )

        plugin = AgentPluginsFormat().load(tmp_path)

        assert plugin.manifest.name == "agent-plugins-example"
        assert [s.name for s in plugin.skills] == ["summarize"]

    def test_mcp_loader_is_deferred(self, tmp_path: Path):
        """mcp.json is not read yet."""
        write_manifest(tmp_path, EXAMPLE_MANIFEST)
        (tmp_path / "mcp.json").write_text(
            json.dumps({"mcpServers": {"x": {"type": "stdio", "command": "echo"}}}),
            encoding="utf-8",
        )

        assert AgentPluginsFormat().load_mcp_config(tmp_path) == {}


class TestClientExtension:
    """Our ``dev.openhands`` namespace: manifest data and extension directory."""

    def test_entry_command_is_mapped_from_our_namespace(self, tmp_path: Path):
        write_manifest(
            tmp_path,
            {
                **EXAMPLE_MANIFEST,
                "extensions": {EXTENSION_NAMESPACE: {"entry_command": "now"}},
            },
        )

        manifest = AgentPluginsFormat().load_manifest(tmp_path)

        assert manifest.entry_command == "now"
        # The raw extension data is preserved alongside the mapped field.
        assert manifest.model_dump()["extensions"] == {
            EXTENSION_NAMESPACE: {"entry_command": "now"}
        }

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(42, id="int"),
            pytest.param([], id="list"),
            pytest.param({"a": 1}, id="object"),
            pytest.param(True, id="bool"),
        ],
    )
    def test_non_string_entry_command_is_reported_and_ignored(
        self, tmp_path: Path, caplog, value
    ):
        """Our namespace, our rules: a bad value drops the field, not the plugin."""
        write_manifest(
            tmp_path,
            {
                **EXAMPLE_MANIFEST,
                "extensions": {EXTENSION_NAMESPACE: {"entry_command": value}},
            },
        )

        manifest = AgentPluginsFormat().load_manifest(tmp_path)

        assert manifest.entry_command is None
        assert "Ignoring non-string" in caplog.text

    @pytest.mark.parametrize(
        "extensions",
        [
            pytest.param({}, id="no-namespaces"),
            pytest.param({EXTENSION_NAMESPACE: {}}, id="ours-empty"),
            pytest.param({EXTENSION_NAMESPACE: {"entry_command": None}}, id="null"),
            pytest.param({EXTENSION_NAMESPACE: {"entry_command": ""}}, id="empty-str"),
            # §8.1: a foreign namespace's contents are never validated, so its
            # entry_command -- of any type -- is not ours to read.
            pytest.param(
                {"com.example.client": {"entry_command": 42}}, id="foreign-namespace"
            ),
        ],
    )
    def test_no_usable_entry_command(self, tmp_path: Path, extensions):
        write_manifest(tmp_path, {**EXAMPLE_MANIFEST, "extensions": extensions})

        assert AgentPluginsFormat().load_manifest(tmp_path).entry_command is None

    def test_components_load_from_the_extension_directory(self, tmp_path: Path):
        write_manifest(tmp_path, EXAMPLE_MANIFEST)
        write_extension_components(tmp_path / EXTENSION_NAMESPACE)

        fmt = AgentPluginsFormat()

        hooks = fmt.load_hooks(tmp_path)
        assert hooks is not None and len(hooks.pre_tool_use) == 1
        assert [a.name for a in fmt.load_agents(tmp_path)] == ["helper"]
        assert [c.name for c in fmt.load_commands(tmp_path)] == ["now"]

    @pytest.mark.parametrize(
        "location",
        [
            pytest.param(None, id="nowhere"),
            pytest.param(".", id="plugin-root"),
            pytest.param("com.example.client", id="foreign-namespace"),
        ],
    )
    def test_components_outside_our_namespace_are_not_read(
        self, tmp_path: Path, location: str | None
    ):
        """§8.2: only the owner of a namespace reads its directory.

        The plugin root holds portable core, and another vendor's directory is
        theirs alone -- components in either place are not ours to load.
        """
        write_manifest(tmp_path, EXAMPLE_MANIFEST)
        if location is not None:
            write_extension_components(tmp_path / location)

        fmt = AgentPluginsFormat()

        assert fmt.load_hooks(tmp_path) is None
        assert fmt.load_agents(tmp_path) == []
        assert fmt.load_commands(tmp_path) == []

    def test_skills_stay_portable_core(self, tmp_path: Path):
        """Skills come from ``<root>/skills``, never from the extension dir."""
        write_manifest(tmp_path, EXAMPLE_MANIFEST)
        skill_dir = tmp_path / EXTENSION_NAMESPACE / "skills" / "summarize"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: summarize\ndescription: Summarize text.\n---\n\nDo it.\n",
            encoding="utf-8",
        )

        assert AgentPluginsFormat().load(tmp_path).skills == []

    def test_broken_components_do_not_sink_the_plugin(self, tmp_path: Path, caplog):
        """§4.1: the failure boundary is one entry, not the plugin.

        A broken agent/command is skipped while its siblings load, and an
        unparseable hooks.json only costs the plugin its hooks.
        """
        write_manifest(tmp_path, EXAMPLE_MANIFEST)
        extension_dir = tmp_path / EXTENSION_NAMESPACE
        write_extension_components(extension_dir)
        (extension_dir / "hooks" / "hooks.json").write_text(
            '{"hooks": { broken }', encoding="utf-8"
        )
        # Unparseable YAML frontmatter: the one failure both loaders share.
        broken_frontmatter = "---\nname: [unclosed\n---\n\nbody\n"
        (extension_dir / "agents" / "broken.md").write_text(
            broken_frontmatter, encoding="utf-8"
        )
        (extension_dir / "commands" / "broken.md").write_text(
            broken_frontmatter, encoding="utf-8"
        )

        plugin = AgentPluginsFormat().load(tmp_path)

        assert plugin.manifest.name == "agent-plugins-example"
        assert [a.name for a in plugin.agents] == ["helper"]
        assert [c.name for c in plugin.commands] == ["now"]
        assert plugin.hooks is None
        assert "Failed to load hooks" in caplog.text
        assert "Failed to load agent" in caplog.text
        assert "Failed to load command" in caplog.text

    def test_hooks_json_without_hooks_is_empty_not_missing(self, tmp_path: Path):
        """An empty-but-valid hooks.json is distinct from an absent one."""
        write_manifest(tmp_path, EXAMPLE_MANIFEST)
        hooks_json = tmp_path / EXTENSION_NAMESPACE / "hooks" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        hooks_json.write_text('{"hooks": {}}', encoding="utf-8")

        hooks = AgentPluginsFormat().load_hooks(tmp_path)

        assert hooks is not None
        assert hooks.is_empty()

    def test_load_assembles_the_full_extension(self, tmp_path: Path):
        write_manifest(
            tmp_path,
            {
                **EXAMPLE_MANIFEST,
                "extensions": {
                    EXTENSION_NAMESPACE: {"entry_command": "now"},
                    "com.example.client": {"whatever": True},
                },
            },
        )
        write_extension_components(tmp_path / EXTENSION_NAMESPACE)

        plugin = AgentPluginsFormat().load(tmp_path)

        assert plugin.entry_slash_command == "/agent-plugins-example:now"
        assert plugin.hooks is not None
        assert [a.name for a in plugin.agents] == ["helper"]
        assert [c.name for c in plugin.commands] == ["now"]


class TestVendoredSchema:
    """The vendored file must stay consistent with the URL we accept."""

    def test_constant_matches_the_literal_url(self):
        assert MANIFEST_SCHEMA_URL == SCHEMA_1_0_0

    def test_schema_identity_matches_constant(self):
        schema = _load_schema(_MANIFEST_SCHEMA_FILE)

        assert schema["$id"] == MANIFEST_SCHEMA_URL
        assert schema["properties"]["$schema"]["const"] == MANIFEST_SCHEMA_URL

    def test_schema_is_closed(self):
        schema = _load_schema(_MANIFEST_SCHEMA_FILE)

        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"$schema", "name"}
