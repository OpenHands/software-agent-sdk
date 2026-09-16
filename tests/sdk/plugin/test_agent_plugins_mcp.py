"""Tests for the Agent Plugins ``mcp.json`` loader."""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.plugin import AgentPluginsFormat, Plugin, get_plugin_data_dir


MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

MANIFEST = {
    "$schema": PLUGIN_SCHEMA,
    "name": "example",
    "version": "1.0.0",
    "description": "An Agent Plugins package.",
}

# The specification's own example, verbatim (§7.2.1).
SPEC_EXAMPLE = {
    "local-validator": {
        "type": "stdio",
        "command": "./bin/validator",
        "args": ["--data", "${PLUGIN_DATA}/validator"],
        "env": {"CONFIG": "${PLUGIN_ROOT}/config.json"},
        "cwd": "${PLUGIN_ROOT}",
    },
    "deployment-api": {
        "type": "streamable-http",
        "url": "https://deploy.example.com/mcp",
        "headers": {"X-Tenant": "public-tenant"},
    },
    "legacy-events": {"type": "sse", "url": "https://legacy.example.com/sse"},
}


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    """A plugin root with a valid manifest and a ``./bin/validator``."""
    root = tmp_path / "example"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "validator").touch()
    (root / "plugin.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    return root


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    return tmp_path / "plugin-data"


def write_mcp(plugin_dir: Path, document: dict | str) -> Path:
    path = plugin_dir / "mcp.json"
    path.write_text(
        document if isinstance(document, str) else json.dumps(document),
        encoding="utf-8",
    )
    return path


def load(plugin_dir: Path, data_root: Path, servers: dict) -> dict[str, MCPServer]:
    """Load ``mcpServers`` under a valid top-level document."""
    write_mcp(plugin_dir, {"$schema": MCP_SCHEMA, "mcpServers": servers})
    return AgentPluginsFormat(plugin_data_root=data_root).load_mcp_config(plugin_dir)


def load_one(plugin_dir: Path, data_root: Path, server: dict) -> MCPServer | None:
    """Load a single entry named ``s``, or None if it was skipped."""
    return load(plugin_dir, data_root, {"s": server}).get("s")


class TestDocument:
    """The top-level document: closed schema, pinned version (§7.2.1)."""

    def test_absent_file_is_not_an_error(self, plugin_dir: Path, data_root: Path):
        fmt = AgentPluginsFormat(plugin_data_root=data_root)

        assert fmt.load_mcp_config(plugin_dir) == {}

    def test_dotted_mcp_json_is_not_read(self, plugin_dir: Path, data_root: Path):
        """The Claude Code filename is a different format's file."""
        (plugin_dir / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"s": {"command": "echo"}}}), encoding="utf-8"
        )

        assert (
            AgentPluginsFormat(plugin_data_root=data_root).load_mcp_config(plugin_dir)
            == {}
        )

    def test_loads_the_spec_example(self, plugin_dir: Path, data_root: Path):
        servers = load(plugin_dir, data_root, SPEC_EXAMPLE)

        # sse is unsupported and skipped; the other two load.
        assert sorted(servers) == ["deployment-api", "local-validator"]

    def test_empty_server_map(self, plugin_dir: Path, data_root: Path):
        assert load(plugin_dir, data_root, {}) == {}

    @pytest.mark.parametrize(
        "document",
        [
            pytest.param("{not json", id="unparseable"),
            pytest.param(json.dumps([]), id="not-an-object"),
            pytest.param(json.dumps({"mcpServers": {}}), id="no-schema"),
            pytest.param(
                json.dumps({"$schema": PLUGIN_SCHEMA, "mcpServers": {}}),
                id="manifest-schema",
            ),
            pytest.param(
                json.dumps(
                    {
                        "$schema": (
                            "https://agent-plugins.org/schemas/2.0.0/mcp.schema.json"
                        ),
                        "mcpServers": {},
                    }
                ),
                id="unsupported-version",
            ),
            pytest.param(json.dumps({"$schema": MCP_SCHEMA}), id="no-servers"),
            pytest.param(
                json.dumps({"$schema": MCP_SCHEMA, "mcpServers": {}, "extra": "field"}),
                id="unknown-top-level-field",
            ),
            pytest.param(
                json.dumps({"$schema": MCP_SCHEMA, "mcpServers": []}),
                id="servers-not-an-object",
            ),
        ],
    )
    def test_invalid_document_disables_mcp(
        self, plugin_dir: Path, data_root: Path, document: str
    ):
        write_mcp(plugin_dir, document)

        assert (
            AgentPluginsFormat(plugin_data_root=data_root).load_mcp_config(plugin_dir)
            == {}
        )

    def test_invalid_document_leaves_other_components_alone(
        self, plugin_dir: Path, data_root: Path
    ):
        """§7.2.2 rule 2: MCP is disabled, the plugin still loads."""
        skill = plugin_dir / "skills" / "greet"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: greet\ndescription: Greet someone.\n---\n\nHi.\n",
            encoding="utf-8",
        )
        write_mcp(plugin_dir, "{not json")

        plugin = Plugin.load(plugin_dir)

        assert plugin.mcp_config == {}
        assert [s.name for s in plugin.skills] == ["greet"]


class TestFailureIsolation:
    """§7.2.2 rules 3-4: one bad entry, one skipped entry."""

    def test_sibling_entries_still_load(self, plugin_dir: Path, data_root: Path):
        servers = load(
            plugin_dir,
            data_root,
            {
                "broken": {"type": "stdio"},
                "unknown-transport": {"type": "carrier-pigeon"},
                "good": {"type": "stdio", "command": "echo"},
            },
        )

        assert sorted(servers) == ["good"]

    @pytest.mark.parametrize(
        "server",
        [
            pytest.param({"type": "stdio", "command": "echo", "url": "x"}, id="mixed"),
            pytest.param(
                {"type": "stdio", "command": "echo", "extra": 1}, id="unknown-field"
            ),
            pytest.param({"command": "echo"}, id="no-type"),
            pytest.param(
                {"type": "stdio", "command": "echo", "env": {"PLUGIN_ROOT": "/x"}},
                id="reserved-env-name",
            ),
            pytest.param(
                {"type": "stdio", "command": "echo", "cwd": "data"},
                id="bare-relative-cwd",
            ),
            pytest.param(
                {"type": "sse", "url": "https://legacy.example.com/sse"},
                id="unsupported-sse",
            ),
        ],
    )
    def test_invalid_entry_is_skipped(
        self, plugin_dir: Path, data_root: Path, server: dict
    ):
        assert load_one(plugin_dir, data_root, server) is None


class TestStdio:
    """§7.2.1 stdio: command resolution, expansion, containment."""

    def test_bare_command_is_left_to_the_platform(
        self, plugin_dir: Path, data_root: Path
    ):
        server = load_one(plugin_dir, data_root, {"type": "stdio", "command": "npx"})

        assert server is not None
        assert server.command == "npx"

    def test_plugin_relative_command_resolves_to_the_package(
        self, plugin_dir: Path, data_root: Path
    ):
        server = load_one(
            plugin_dir, data_root, {"type": "stdio", "command": "./bin/validator"}
        )

        assert server is not None
        assert server.command == str(plugin_dir.resolve() / "bin" / "validator")

    @pytest.mark.parametrize(
        "command",
        ["../outside", "/usr/bin/outside", "bin/validator", "./../outside"],
    )
    def test_command_must_be_bare_or_contained(
        self, plugin_dir: Path, data_root: Path, command: str
    ):
        assert (
            load_one(plugin_dir, data_root, {"type": "stdio", "command": command})
            is None
        )

    def test_command_is_never_expanded(self, plugin_dir: Path, data_root: Path):
        """§9.2: expansion does not apply to ``command``."""
        server = load_one(
            plugin_dir, data_root, {"type": "stdio", "command": "${PLUGIN_ROOT}"}
        )

        assert server is not None
        assert server.command == "${PLUGIN_ROOT}"

    def test_expands_args_and_env_values(self, plugin_dir: Path, data_root: Path):
        server = load_one(
            plugin_dir,
            data_root,
            {
                "type": "stdio",
                "command": "echo",
                "args": ["--root=${PLUGIN_ROOT}", "${PLUGIN_DATA}/cache", "plain"],
                "env": {"CONFIG": "${PLUGIN_ROOT}/config.json"},
            },
        )

        assert server is not None
        root = str(plugin_dir.resolve())
        data = str(get_plugin_data_dir("example", data_root=data_root))
        assert server.args == [f"--root={root}", f"{data}/cache", "plain"]
        assert server.env is not None
        assert server.env["CONFIG"].get_secret_value() == f"{root}/config.json"

    def test_other_placeholders_stay_literal(self, plugin_dir: Path, data_root: Path):
        """§9.2: no environment, secret or default expansion of any kind."""
        server = load_one(
            plugin_dir,
            data_root,
            {
                "type": "stdio",
                "command": "echo",
                "args": ["${HOME}", "${MISSING:-fallback}", "$PLUGIN_ROOT"],
            },
        )

        assert server is not None
        assert server.args == ["${HOME}", "${MISSING:-fallback}", "$PLUGIN_ROOT"]

    def test_expansion_is_not_recursive(self, plugin_dir: Path, data_root: Path):
        """Text a replacement introduces is not rescanned."""
        server = load_one(
            plugin_dir,
            data_root,
            {"type": "stdio", "command": "echo", "args": ["${PLUGIN_${PLUGIN_ROOT}"]},
        )

        assert server is not None
        assert server.args == [f"${{PLUGIN_{plugin_dir.resolve()}"]

    def test_reserved_variables_are_set_last(self, plugin_dir: Path, data_root: Path):
        """§9.1: the client supplies them and the plugin cannot override them."""
        server = load_one(
            plugin_dir,
            data_root,
            {"type": "stdio", "command": "echo", "env": {"OTHER": "value"}},
        )

        assert server is not None
        assert server.env is not None
        assert server.env["PLUGIN_ROOT"].get_secret_value() == str(plugin_dir.resolve())
        assert server.env["PLUGIN_DATA"].get_secret_value() == str(
            get_plugin_data_dir("example", data_root=data_root)
        )

    def test_default_cwd_is_the_plugin_root(self, plugin_dir: Path, data_root: Path):
        server = load_one(plugin_dir, data_root, {"type": "stdio", "command": "echo"})

        assert server is not None
        assert server.cwd == str(plugin_dir.resolve())

    @pytest.mark.parametrize(
        ("cwd", "expected"),
        [
            ("./data", "root/data"),
            ("${PLUGIN_ROOT}", "root"),
            ("${PLUGIN_ROOT}/data", "root/data"),
            ("${PLUGIN_DATA}", "data"),
            ("${PLUGIN_DATA}/cache", "data/cache"),
        ],
    )
    def test_cwd_forms(
        self, plugin_dir: Path, data_root: Path, cwd: str, expected: str
    ):
        server = load_one(
            plugin_dir, data_root, {"type": "stdio", "command": "echo", "cwd": cwd}
        )

        roots = {
            "root": plugin_dir.resolve(),
            "data": get_plugin_data_dir("example", data_root=data_root).resolve(),
        }
        head, _, tail = expected.partition("/")
        assert server is not None
        assert server.cwd == str(roots[head] / tail if tail else roots[head])

    @pytest.mark.parametrize(
        "cwd", ["./../outside", "${PLUGIN_ROOT}/../outside", "${PLUGIN_DATA}/../other"]
    )
    def test_cwd_may_not_escape_its_root(
        self, plugin_dir: Path, data_root: Path, cwd: str
    ):
        assert (
            load_one(
                plugin_dir, data_root, {"type": "stdio", "command": "echo", "cwd": cwd}
            )
            is None
        )


class TestRemote:
    """§7.2.1 remote endpoints: URL rules and literal headers."""

    def test_streamable_http_is_mapped(self, plugin_dir: Path, data_root: Path):
        server = load_one(
            plugin_dir,
            data_root,
            {
                "type": "streamable-http",
                "url": "https://deploy.example.com/mcp",
                "headers": {"X-Tenant": "public-tenant"},
            },
        )

        assert server is not None
        assert server.transport == "streamable-http"
        assert server.url == "https://deploy.example.com/mcp"
        assert server.headers is not None
        assert server.headers["X-Tenant"].get_secret_value() == "public-tenant"

    def test_loopback_may_use_http(self, plugin_dir: Path, data_root: Path):
        server = load_one(
            plugin_dir,
            data_root,
            {"type": "streamable-http", "url": "http://127.0.0.1:3000/mcp"},
        )

        assert server is not None

    @pytest.mark.parametrize(
        "url",
        [
            pytest.param("/mcp", id="relative"),
            pytest.param("ftp://example.com/mcp", id="scheme"),
            pytest.param("http://deploy.example.com/mcp", id="remote-plaintext"),
            pytest.param("https://user:pw@example.com/mcp", id="user-info"),
            pytest.param("https://example.com/mcp#frag", id="fragment"),
        ],
    )
    def test_invalid_url_skips_the_entry(
        self, plugin_dir: Path, data_root: Path, url: str
    ):
        assert (
            load_one(plugin_dir, data_root, {"type": "streamable-http", "url": url})
            is None
        )

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({"X Tenant": "v"}, id="space-in-name"),
            pytest.param({"X-Tenant": "v", "x-tenant": "w"}, id="case-duplicate"),
            pytest.param({"X-Tenant": "line\nbreak"}, id="value-newline"),
        ],
    )
    def test_invalid_headers_skip_the_entry(
        self, plugin_dir: Path, data_root: Path, headers: dict
    ):
        assert (
            load_one(
                plugin_dir,
                data_root,
                {
                    "type": "streamable-http",
                    "url": "https://example.com/mcp",
                    "headers": headers,
                },
            )
            is None
        )

    def test_headers_are_never_expanded(self, plugin_dir: Path, data_root: Path):
        server = load_one(
            plugin_dir,
            data_root,
            {
                "type": "streamable-http",
                "url": "https://example.com/mcp",
                "headers": {"X-Root": "${PLUGIN_ROOT}"},
            },
        )

        assert server is not None
        assert server.headers is not None
        assert server.headers["X-Root"].get_secret_value() == "${PLUGIN_ROOT}"


class TestLiteralValues:
    """Package data is visible, so it must survive serialization unredacted."""

    def test_plugin_values_serialize_as_written(
        self, plugin_dir: Path, data_root: Path
    ):
        servers = load(
            plugin_dir,
            data_root,
            {
                "api": {
                    "type": "streamable-http",
                    "url": "https://example.com/mcp",
                    "headers": {"X-Tenant": "public-tenant"},
                },
                "local": {
                    "type": "stdio",
                    "command": "echo",
                    "env": {"CONFIG": "visible"},
                },
            },
        )

        # No serialization context: what storage and a remote conversation get.
        api = servers["api"].model_dump(mode="json")
        local = servers["local"].model_dump(mode="json")
        assert servers["api"].literal_values is True
        assert api["headers"] == {"X-Tenant": "public-tenant"}
        assert local["env"]["CONFIG"] == "visible"

    def test_ordinary_servers_are_still_redacted(self):
        server = MCPServer(
            transport="streamable-http",
            url="https://example.com/mcp",
            headers={"Authorization": SecretStr("Bearer t0ken")},
        )

        assert not server.literal_values
        assert server.model_dump(mode="json")["headers"] == {
            "Authorization": "**********"
        }


class TestPluginData:
    """§9.1: a writable directory that outlives an update."""

    def test_created_and_keyed_by_manifest_name(
        self, plugin_dir: Path, data_root: Path
    ):
        load(plugin_dir, data_root, {"s": {"type": "stdio", "command": "echo"}})

        assert (data_root / "example").is_dir()

    def test_lives_outside_the_plugin_package(self, plugin_dir: Path, data_root: Path):
        """Anything inside the package would be lost on update."""
        load(plugin_dir, data_root, {"s": {"type": "stdio", "command": "echo"}})

        assert not (data_root / "example").is_relative_to(plugin_dir.resolve())
