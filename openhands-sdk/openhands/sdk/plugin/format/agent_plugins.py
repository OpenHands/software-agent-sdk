"""The Agent Plugins format (agent-plugins.org).

Loads a root-level ``plugin.json`` against a closed JSON Schema that is
vendored locally and selected by the manifest's own ``$schema`` -- never
fetched over the network.

See the ``openhands.sdk.plugin.format`` package docstring for the design.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any, ClassVar, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.plugin.format.base import PluginFormat
from openhands.sdk.plugin.types import CommandDefinition, PluginManifest
from openhands.sdk.subagent.schema import AgentDefinition


logger = get_logger(__name__)

# At the plugin root, not nested as in the Claude Code layout.
MANIFEST_FILE: Final[str] = "plugin.json"

_SCHEMAS_DIR: Final[Path] = Path(__file__).parent / "schemas"

#: The only manifest ``$schema`` we support, and its vendored file. Agent
#: Plugins also publishes an ``mcp.schema.json``, but that one belongs to the
#: ``mcp.json`` loader: keying both here would let a manifest declaring the MCP
#: schema validate against it.
MANIFEST_SCHEMA_URL: Final[str] = (
    "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
)
_MANIFEST_SCHEMA_FILE: Final[str] = "plugin-1.0.0.schema.json"


@cache
def _load_schema(filename: str) -> dict[str, Any]:
    """Read a vendored schema. Cached; never fetched over the network."""
    return json.loads((_SCHEMAS_DIR / filename).read_text(encoding="utf-8"))


class AgentPluginsFormat(PluginFormat):
    """The Agent Plugins v1.0.0 portable package layout.

    ```
    plugin-name/
    ├── plugin.json              # required manifest (root-level, closed schema)
    ├── skills/                  # Agent Skills (optional)
    ├── mcp.json                 # MCP servers, no leading dot (optional)
    └── com.example.client/      # reverse-domain client extensions (optional)
    ```

    Only the manifest and skills are read today (skills via the shared
    :meth:`PluginFormat.load_skills`). ``mcp.json`` and OpenHands' client
    extension are follow-ups under #4405, so their loaders return empty --
    spec-correct, since a client must ignore extensions it does not implement.

    Hooks, commands, agents and ``entry_command`` are not portable core. The
    standard's place for them is a client extension: a reverse-domain namespace
    carrying manifest data under ``extensions.<ns>``, a top-level ``<ns>/``
    directory, or both, whose contents the namespace owner defines. Which
    namespace we claim and what goes where are still open in #4405, and nothing
    here reads one yet.

    Not registered in ``_FORMATS`` yet; see the package docstring.
    """

    name: ClassVar[str] = "agent-plugins"

    @classmethod
    def detect(cls, plugin_dir: Path) -> bool:
        # Presence is the whole rule: a broken manifest (bad JSON, missing or
        # unsupported $schema) must be claimed and rejected by load_manifest(),
        # not fall through to the Claude Code format, which would load it under
        # a name inferred from the directory.
        return (plugin_dir / MANIFEST_FILE).is_file()

    def load_manifest(self, plugin_dir: Path) -> PluginManifest:
        """Load and validate the root ``plugin.json``.

        Violations are fatal except the two the spec marks non-fatal, which are
        reported and ignored: unknown top-level fields, and a non-object
        ``extensions``.

        Fields with no ``PluginManifest`` column (``$schema``, ``homepage``,
        ``repository``, ``license``, ``keywords``, ``extensions``) survive via
        ``extra="allow"``.

        Raises:
            ValueError: On any fatal manifest violation.
        """
        manifest_path = plugin_dir / MANIFEST_FILE
        try:
            # utf-8-sig: tolerate a leading BOM, which RFC 8259 lets us ignore.
            data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {manifest_path}: {e}") from e
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"Failed to read manifest {manifest_path}: {e}") from e

        if not isinstance(data, dict):
            raise ValueError(
                f"Manifest {manifest_path} must contain a JSON object, "
                f"got {type(data).__name__}"
            )

        schema_url = data.get("$schema")
        if schema_url != MANIFEST_SCHEMA_URL:
            raise ValueError(
                f"Unsupported or missing $schema in {manifest_path}: "
                f"{schema_url!r}. Supported: {MANIFEST_SCHEMA_URL}"
            )
        schema = _load_schema(_MANIFEST_SCHEMA_FILE)

        # Non-fatal: report and ignore. Known fields come from the schema, so
        # there is no second field list to drift.
        unknown = sorted(set(data) - set(schema["properties"]))
        if unknown:
            logger.warning(
                "Ignoring unknown top-level field(s) in %s: %s",
                manifest_path,
                ", ".join(unknown),
            )
            data = {k: v for k, v in data.items() if k not in unknown}

        if "extensions" in data and not isinstance(data["extensions"], dict):
            logger.warning("Ignoring non-object 'extensions' in %s", manifest_path)
            data = {k: v for k, v in data.items() if k != "extensions"}

        # Everything else is fatal. Name constraints come from the vendored
        # pattern, not a Python re-implementation.
        try:
            Draft202012Validator(schema).validate(data)
        except JSONSchemaValidationError as e:
            raise ValueError(
                f"Invalid Agent Plugins manifest {manifest_path}: {e.message}"
            ) from e

        return PluginManifest.model_validate(data)

    def load_mcp_config(self, plugin_dir: Path) -> dict[str, MCPServer]:  # noqa: ARG002
        """Not read yet: root ``mcp.json`` is a follow-up."""
        return {}

    def load_hooks(self, plugin_dir: Path) -> HookConfig | None:  # noqa: ARG002
        """Always None: hooks are not part of the Agent Plugins portable core."""
        return None

    def load_agents(self, plugin_dir: Path) -> list[AgentDefinition]:  # noqa: ARG002
        """Always empty: agents are not part of the Agent Plugins portable core."""
        return []

    def load_commands(self, plugin_dir: Path) -> list[CommandDefinition]:  # noqa: ARG002
        """Always empty: commands are not part of the Agent Plugins portable core."""
        return []
