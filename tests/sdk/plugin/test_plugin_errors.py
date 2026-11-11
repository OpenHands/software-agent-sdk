"""Tests for PluginManifestError and PluginNameError."""

import json
from pathlib import Path

import pytest

from openhands.sdk.plugin.errors import PluginManifestError, PluginNameError
from openhands.sdk.plugin.format.agent_plugins import (
    AgentPluginsFormat,
    _validate_plugin_name,
)


class TestPluginManifestError:
    """PluginManifestError carries structured context for HTTP responses."""

    def test_is_value_error_subclass(self):
        """Backward compat: existing except ValueError handlers still catch it."""
        err = PluginManifestError("test-plugin", "bad schema", "fix it")
        assert isinstance(err, ValueError)

    def test_fields_stored(self):
        err = PluginManifestError("my-plugin", "missing $schema", "add $schema")
        assert err.plugin_name == "my-plugin"
        assert err.failure_reason == "missing $schema"
        assert err.remediation == "add $schema"
        assert err.error_id is not None

    def test_error_id_is_uuid(self):
        err = PluginManifestError("p", "r", "m")
        # Should be a valid UUID
        import uuid
        uuid.UUID(err.error_id)

    def test_custom_error_id(self):
        err = PluginManifestError("p", "r", "m", error_id="custom-123")
        assert err.error_id == "custom-123"

    def test_message_contains_all_fields(self):
        err = PluginManifestError("my-plugin", "bad $schema", "add $schema")
        msg = str(err)
        assert "my-plugin" in msg
        assert "bad $schema" in msg
        assert "add $schema" in msg
        assert "error_id" in msg

    def test_to_dict_serialization(self):
        err = PluginManifestError("p", "r", "m", error_id="e123")
        d = err.to_dict()
        assert d == {
            "plugin_name": "p",
            "failure_reason": "r",
            "remediation": "m",
            "error_id": "e123",
        }

    def test_unique_error_ids(self):
        err1 = PluginManifestError("p", "r", "m")
        err2 = PluginManifestError("p", "r", "m")
        assert err1.error_id != err2.error_id


class TestPluginNameError:
    """PluginNameError rejects unsafe plugin names."""

    def test_is_value_error_subclass(self):
        err = PluginNameError("evil;rm -rf /")
        assert isinstance(err, ValueError)

    def test_fields_stored(self):
        err = PluginNameError("bad;name")
        assert err.plugin_name == "bad;name"
        assert err.error_id is not None

    def test_to_dict(self):
        err = PluginNameError("x", error_id="e1")
        assert err.to_dict() == {"plugin_name": "x", "error_id": "e1"}


class TestValidatePluginName:
    """_validate_plugin_name rejects unsafe names."""

    @pytest.mark.parametrize("name", [
        "valid-name",
        "valid_name",
        "ValidName123",
        "my.plugin",
        "a",
    ])
    def test_safe_names_accepted(self, name):
        assert _validate_plugin_name(name) == name

    @pytest.mark.parametrize("name", [
        "; rm -rf /",
        "evil|name",
        "name&background",
        "name`cmd`",
        "$(rm -rf /)",
        "name\nnewline",
        "name\rcarriage",
        "name<redirect",
        "name>redirect",
        "name*wildcard",
        "name?question",
        "name[bracket]",
        "name{brace}",
        "name\\backslash",
        "name(paren)",
        "name!bang",
        "name#hash",
        "name~tilde",
        "/etc/passwd",
        "../../etc/shadow",
        ".",
        "..",
        "/",
        "",
    ])
    def test_unsafe_names_rejected(self, name):
        with pytest.raises(PluginNameError):
            _validate_plugin_name(name)

    def test_non_string_rejected(self):
        with pytest.raises(PluginNameError):
            _validate_plugin_name(123)  # type: ignore

    def test_none_rejected(self):
        with pytest.raises(PluginNameError):
            _validate_plugin_name(None)  # type: ignore


class TestAgentPluginsFormatErrorIntegration:
    """AgentPluginsFormat.load_manifest raises PluginManifestError."""

    def test_missing_schema_raises_plugin_manifest_error(self, tmp_path):
        """The compound-engineering case: plugin.json without $schema."""
        plugin_dir = tmp_path / "compound-engineering"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "compound-engineering",
            "version": "3.30.4",
        }))

        fmt = AgentPluginsFormat()
        with pytest.raises(PluginManifestError) as exc_info:
            fmt.load_manifest(plugin_dir)

        err = exc_info.value
        assert err.plugin_name == "compound-engineering"
        assert "missing or unsupported $schema" in err.failure_reason
        assert "agent-plugins.org" in err.remediation
        assert err.error_id is not None

    def test_wrong_schema_raises_plugin_manifest_error(self, tmp_path):
        plugin_dir = tmp_path / "wrong-schema"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "test",
            "version": "1.0.0",
            "$schema": "https://wrong.example.com/schema.json",
        }))

        fmt = AgentPluginsFormat()
        with pytest.raises(PluginManifestError) as exc_info:
            fmt.load_manifest(plugin_dir)

        assert "test" in str(exc_info.value)

    def test_unsafe_plugin_name_raises_plugin_name_error(self, tmp_path):
        """Plugin name with shell metacharacters is rejected."""
        plugin_dir = tmp_path / "evil-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "; rm -rf / #",
            "version": "1.0.0",
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        }))

        fmt = AgentPluginsFormat()
        with pytest.raises(PluginNameError):
            fmt.load_manifest(plugin_dir)

    def test_valid_manifest_loads(self, tmp_path):
        """A valid manifest with correct $schema loads successfully."""
        plugin_dir = tmp_path / "valid-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "valid-plugin",
            "version": "1.0.0",
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        }))

        fmt = AgentPluginsFormat()
        manifest = fmt.load_manifest(plugin_dir)
        assert manifest.name == "valid-plugin"
