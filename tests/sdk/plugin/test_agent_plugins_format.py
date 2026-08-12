"""Tests for the Agent Plugins format: detection and the manifest loader."""

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
    VENDORED_SCHEMAS,
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
    """Skills are shared with the base; the rest are follow-ups."""

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

    def test_deferred_component_loaders_are_empty(self, tmp_path: Path):
        """mcp.json and client extensions are not read yet."""
        write_manifest(tmp_path, EXAMPLE_MANIFEST)
        (tmp_path / "mcp.json").write_text(
            json.dumps({"mcpServers": {"x": {"type": "stdio", "command": "echo"}}}),
            encoding="utf-8",
        )

        fmt = AgentPluginsFormat()

        assert fmt.load_mcp_config(tmp_path) == {}
        assert fmt.load_hooks(tmp_path) is None
        assert fmt.load_agents(tmp_path) == []
        assert fmt.load_commands(tmp_path) == []


class TestVendoredSchema:
    """The vendored file must stay consistent with the URL it is keyed by."""

    @pytest.mark.parametrize("url,filename", sorted(VENDORED_SCHEMAS.items()))
    def test_schema_identity_matches_key(self, url: str, filename: str):
        schema = _load_schema(filename)

        assert schema["$id"] == url
        assert schema["properties"]["$schema"]["const"] == url

    @pytest.mark.parametrize("filename", sorted(VENDORED_SCHEMAS.values()))
    def test_schema_is_closed(self, filename: str):
        schema = _load_schema(filename)

        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"$schema", "name"}
