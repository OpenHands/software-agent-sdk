"""The Agent Plugins client conformance checklist, driven by real packages.

Every test here loads a checked-in package from
``tests/fixtures/plugins/agent_plugins/`` through the public
:meth:`Plugin.load` entry point -- no manifests built inline, no monkeypatched
format registry, no network.

The six items are the loading sequence a conformant client implements
(https://agent-plugins.org/client-implementers, Appendix A of the spec):

Item 1  Establish the filesystem-resolved plugin root
        -> ``TestRootEstablishment``
Item 2  Select the locally supported ``$schema``; never retrieve one
        -> ``TestSchemaSelection``
Item 3  Reject fatal violations; report and ignore the non-fatal ones
        -> ``TestManifestRejection``
Item 4  Discover each supported component type from its fixed location
        -> ``TestFixedLocations``, ``TestPlaceholderExpansion``
Item 5  Apply each component's failure boundary
        -> ``TestFailureBoundaries``
Item 6  Apply implemented extension namespaces, ignore all others
        -> ``TestClientExtensions``
"""

import socket
import sys
from pathlib import Path

import pytest

from openhands.sdk.plugin import AgentPluginsFormat, Plugin, detect_format
from openhands.sdk.plugin.format.agent_plugins_mcp import get_plugin_data_dir


FIXTURES = Path(__file__).parents[2] / "fixtures" / "plugins" / "agent_plugins"


def fixture(name: str) -> Path:
    """Return a corpus package, resolved as a caller would pass it."""
    return (FIXTURES / name).resolve()


@pytest.fixture(autouse=True)
def plugin_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep ``PLUGIN_DATA`` out of the real user directory.

    ``Plugin.load()`` builds the format itself, so the data root is redirected
    at the module default rather than injected through the constructor.
    """
    root = tmp_path / "plugin-data"
    monkeypatch.setattr(
        "openhands.sdk.plugin.format.agent_plugins_mcp.DEFAULT_PLUGIN_DATA_DIR", root
    )
    return root


def env_of(plugin: Plugin, server: str) -> dict[str, str]:
    return {
        key: value.get_secret_value()
        for key, value in (plugin.mcp_config[server].env or {}).items()
    }


def args_of(plugin: Plugin, server: str) -> list[str]:
    return plugin.mcp_config[server].args or []


def cwd_of(plugin: Plugin, server: str) -> str:
    return plugin.mcp_config[server].cwd or ""


def headers_of(plugin: Plugin, server: str) -> dict[str, str]:
    return {
        key: value.get_secret_value()
        for key, value in (plugin.mcp_config[server].headers or {}).items()
    }


class TestVendoredExample:
    """The official example package, loaded from vendored upstream bytes."""

    def test_loads_as_an_agent_plugins_package(self):
        plugin = Plugin.load(fixture("agent-plugins-example"))

        assert isinstance(
            detect_format(fixture("agent-plugins-example")), AgentPluginsFormat
        )
        assert plugin.name == "agent-plugins-example"
        assert plugin.version == "1.0.0"
        assert [skill.name for skill in plugin.skills] == ["migrate-agent-plugin"]

    def test_skill_resources_come_along(self):
        """Upstream ships the skill's references/; they must survive loading."""
        plugin = Plugin.load(fixture("agent-plugins-example"))

        resources = plugin.skills[0].resources
        assert resources is not None
        assert sorted(resources.references) == [
            "client-extensions.md",
            "migration-guide.md",
            "validation-checklist.md",
        ]

    def test_skills_only_package_contributes_nothing_else(self):
        """It has no mcp.json and no extension directory (spec §6.2)."""
        plugin = Plugin.load(fixture("agent-plugins-example"))

        assert plugin.mcp_config == {}
        assert plugin.agents == []
        assert plugin.commands == []
        assert plugin.entry_slash_command is None


class TestRootEstablishment:
    """Item 1: establish the filesystem-resolved plugin root (§4.1)."""

    def test_root_is_the_directory_the_package_lives_in(self):
        plugin = Plugin.load(fixture("full-package"))

        assert Path(plugin.path) == fixture("full-package")
        assert args_of(plugin, "local-tools")[1] == str(fixture("full-package"))

    @pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges")
    def test_root_is_resolved_before_anything_uses_it(
        self, tmp_path: Path, plugin_data_root: Path
    ):
        """Reached through a symlink, the package still loads as its real root."""
        link = tmp_path / "link-to-full-package"
        link.symlink_to(fixture("full-package"), target_is_directory=True)

        plugin = Plugin.load(link)

        assert Path(plugin.path) == fixture("full-package")
        assert args_of(plugin, "local-tools")[1] == str(fixture("full-package"))
        assert cwd_of(plugin, "local-tools").startswith(
            str(
                get_plugin_data_dir(fixture("full-package"), data_root=plugin_data_root)
            )
        )

    def test_package_paths_stay_inside_the_root(self, caplog):
        """A ``./`` command that climbs out of the package is not launched."""
        plugin = Plugin.load(fixture("partial-failures"))

        assert "escaping-command" not in plugin.mcp_config
        assert "escapes" in caplog.text


class TestSchemaSelection:
    """Item 2: select local rules from ``$schema``, never retrieve one."""

    def test_no_socket_is_opened_while_loading(self, monkeypatch: pytest.MonkeyPatch):
        """The vendored schema is used; the ``$schema`` URL is an identifier."""

        def forbidden(*args, **kwargs):
            raise AssertionError("plugin loading must not touch the network")

        monkeypatch.setattr(socket, "socket", forbidden)
        monkeypatch.setattr(socket, "create_connection", forbidden)
        monkeypatch.setattr(socket, "getaddrinfo", forbidden)

        plugin = Plugin.load(fixture("full-package"))

        assert [skill.name for skill in plugin.skills] == ["greet", "summarize"]
        assert set(plugin.mcp_config) == {"local-tools", "remote-api"}

    def test_a_published_but_unvendored_version_is_rejected(self):
        """Upstream publishes 1.1.0; this SDK vendors only 1.0.0."""
        with pytest.raises(ValueError, match=r"Unsupported or missing \$schema"):
            Plugin.load(fixture("unsupported-schema"))

    def test_rejection_names_the_version_we_support(self):
        with pytest.raises(ValueError, match="schemas/1.0.0/plugin.schema.json"):
            Plugin.load(fixture("unsupported-schema"))


class TestManifestRejection:
    """Item 3: fatal violations reject; the two non-fatal ones are ignored."""

    def test_a_fatal_violation_rejects_the_whole_package(self):
        with pytest.raises(ValueError, match="Invalid Agent Plugins manifest"):
            Plugin.load(fixture("fatal-manifest"))

    def test_a_rejected_package_never_reaches_component_discovery(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """§11.3 rule 2: no component of a rejected plugin may be discovered."""
        discovered: list[str] = []
        for loader in (
            "load_skills",
            "load_mcp_config",
            "load_agents",
            "load_commands",
            "load_hooks",
        ):
            monkeypatch.setattr(
                AgentPluginsFormat,
                loader,
                lambda self, plugin_dir, _name=loader: discovered.append(_name),
            )

        with pytest.raises(ValueError):
            Plugin.load(fixture("fatal-manifest"))

        assert discovered == []

    def test_non_fatal_violations_are_reported_and_ignored(self, caplog):
        """An unknown top-level field and a non-object ``extensions`` (§5.2, §8.1)."""
        plugin = Plugin.load(fixture("non-fatal-manifest"))

        assert [skill.name for skill in plugin.skills] == ["ok"]
        assert "futureField" in caplog.text
        assert "non-object 'extensions'" in caplog.text


class TestFixedLocations:
    """Item 4: discover each supported component type from its fixed location."""

    def test_every_supported_component_type_is_discovered(self):
        plugin = Plugin.load(fixture("full-package"))

        assert [skill.name for skill in plugin.skills] == ["greet", "summarize"]
        assert set(plugin.mcp_config) == {"local-tools", "remote-api"}
        assert [agent.name for agent in plugin.agents] == ["full-package-helper"]
        assert [command.name for command in plugin.commands] == ["report"]
        assert plugin.hooks is not None and not plugin.hooks.is_empty()

    def test_missing_locations_are_a_valid_absence(self):
        """§6.2: a manifest-only package is conformant, not broken."""
        plugin = Plugin.load(fixture("manifest-only"))

        assert plugin.name == "manifest-only"
        assert plugin.skills == []
        assert plugin.mcp_config == {}
        assert plugin.agents == []
        assert plugin.commands == []
        assert plugin.hooks is None

    def test_the_claude_code_locations_are_not_searched(self):
        """Fixed means fixed: ``.mcp.json`` and ``hooks/`` at the root are not ours."""
        plugin = Plugin.load(fixture("full-package"))

        assert not (Path(plugin.path) / ".mcp.json").exists()
        assert not (Path(plugin.path) / "hooks").exists()
        assert not (Path(plugin.path) / "commands").exists()


class TestPlaceholderExpansion:
    """Item 4's runtime half: §9.2 expansion on the server that gets built."""

    def test_expanded_in_args_env_values_and_cwd(self, plugin_data_root: Path):
        plugin = Plugin.load(fixture("full-package"))
        root = str(fixture("full-package"))
        data = str(
            get_plugin_data_dir(fixture("full-package"), data_root=plugin_data_root)
        )

        assert args_of(plugin, "local-tools") == [
            "--root",
            root,
            "--cache",
            f"{data}/cache",
        ]
        assert env_of(plugin, "local-tools")["TOOLS_STATE"] == f"{data}/state"
        assert cwd_of(plugin, "local-tools") == f"{data}/work"

    def test_both_variables_are_provided_to_the_subprocess(
        self, plugin_data_root: Path
    ):
        """§9.1: the client sets them last, and the plugin cannot override them."""
        plugin = Plugin.load(fixture("full-package"))
        env = env_of(plugin, "local-tools")

        assert env["PLUGIN_ROOT"] == str(fixture("full-package"))
        assert env["PLUGIN_DATA"] == str(
            get_plugin_data_dir(fixture("full-package"), data_root=plugin_data_root)
        )

    def test_plugin_data_exists_before_the_subprocess_would_launch(
        self, plugin_data_root: Path
    ):
        Plugin.load(fixture("full-package"))

        assert get_plugin_data_dir(
            fixture("full-package"), data_root=plugin_data_root
        ).is_dir()

    def test_left_untouched_everywhere_else(self):
        """Command, env keys, URL and headers are never expanded."""
        plugin = Plugin.load(fixture("full-package"))

        assert plugin.mcp_config["local-tools"].command == str(
            fixture("full-package") / "bin" / "serve.sh"
        )
        assert "${PLUGIN_ROOT}" in env_of(plugin, "local-tools")
        assert plugin.mcp_config["remote-api"].url == (
            "https://api.example.com/mcp?root=${PLUGIN_ROOT}"
        )
        assert headers_of(plugin, "remote-api") == {
            "Authorization": "Bearer ${PLUGIN_DATA}",
            "X-Plugin-Root": "${PLUGIN_ROOT}",
        }

    def test_other_placeholder_like_text_stays_literal(self):
        assert (
            env_of(Plugin.load(fixture("full-package")), "local-tools")["TOOLS_LABEL"]
            == "literal ${NOT_A_PLACEHOLDER} text"
        )


class TestFailureBoundaries:
    """Item 5: apply the boundary defined for each component type or entry."""

    def test_one_broken_skill_does_not_take_its_siblings(self, caplog):
        plugin = Plugin.load(fixture("partial-failures"))

        assert [skill.name for skill in plugin.skills] == ["good"]
        assert "broken" in caplog.text

    @pytest.mark.parametrize(
        "server", ["legacy-sse", "escaping-command", "placeholder-command"]
    )
    def test_one_bad_mcp_entry_does_not_take_its_siblings(self, server: str):
        """Unsupported transport, escaping command, unexpandable command (§7.2.2)."""
        plugin = Plugin.load(fixture("partial-failures"))

        assert server not in plugin.mcp_config
        assert set(plugin.mcp_config) == {"good-server"}

    def test_a_bad_mcp_entry_does_not_take_another_component_type(self):
        """Skills and agents load beside three skipped MCP entries."""
        plugin = Plugin.load(fixture("partial-failures"))

        assert [skill.name for skill in plugin.skills] == ["good"]
        assert [agent.name for agent in plugin.agents] == ["partial-failures-helper"]

    def test_one_broken_agent_does_not_take_its_siblings(self):
        plugin = Plugin.load(fixture("partial-failures"))

        assert [agent.name for agent in plugin.agents] == ["partial-failures-helper"]


class TestClientExtensions:
    """Item 6: apply our namespace, ignore every other one unvalidated."""

    def test_our_manifest_data_is_applied(self):
        plugin = Plugin.load(fixture("full-package"))

        assert plugin.entry_slash_command == "/full-package:report"

    def test_our_extension_directory_is_read(self):
        """§8.2: file-based components live under ``dev.openhands/``."""
        plugin = Plugin.load(fixture("full-package"))

        assert [command.name for command in plugin.commands] == ["report"]
        assert [agent.name for agent in plugin.agents] == ["full-package-helper"]
        assert plugin.hooks is not None
        assert [matcher.matcher for matcher in plugin.hooks.pre_tool_use] == [
            "full-package-*"
        ]

    def test_a_foreign_namespace_is_ignored_without_validation(self):
        """``com.example.other`` carries an ``entry_command`` of the wrong type.

        §8.1 says that is none of our business: it must neither be applied nor
        rejected.
        """
        plugin = Plugin.load(fixture("full-package"))

        assert plugin.entry_slash_command == "/full-package:report"
        assert plugin.manifest.model_extra is not None
        assert plugin.manifest.model_extra["extensions"]["com.example.other"] == {
            "entry_command": 17,
            "anything": [1, 2, 3],
        }

    def test_a_package_without_our_namespace_still_loads(self):
        plugin = Plugin.load(fixture("partial-failures"))

        assert plugin.entry_slash_command is None
