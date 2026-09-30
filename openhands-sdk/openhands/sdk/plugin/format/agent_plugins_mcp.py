"""The Agent Plugins ``mcp.json`` loader (spec §7.2, §9).

Reads a root-level ``mcp.json`` (no dot) and maps each entry onto an
:class:`~openhands.sdk.mcp.config.MCPServer`. The mapping is not a rename: the
portable format carries semantics our model does not, so the work here is
placeholder expansion (§9.2), path containment (§4.1, §7.2.1) and remote URL /
header validation — all applied per entry, so one bad server never takes down
its siblings or another component type (§7.2.2).

Lives beside ``agent_plugins.py`` rather than inside it: the manifest and the
MCP configuration are separate documents with separate schemas and separate
failure boundaries.
"""

import ipaddress
import json
import re
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError
from pydantic import SecretStr, ValidationError

from openhands.sdk.extensions.fetch import get_cache_path
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.plugin.format.base import _load_schema
from openhands.sdk.utils.path import get_user_persistence_dir, resolves_within


logger = get_logger(__name__)

#: At the plugin root, with no leading dot (unlike the Claude Code ``.mcp.json``).
MCP_FILE: Final[str] = "mcp.json"

MCP_SCHEMA_URL: Final[str] = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
_MCP_SCHEMA_FILE: Final[str] = "mcp-1.0.0.schema.json"

#: Transports we connect with. ``sse`` is OPTIONAL in the spec and deprecated by
#: MCP itself, so an ``sse`` entry is skipped as an unsupported transport
#: (§7.2.2 rule 4) rather than quietly served over another transport.
SUPPORTED_TRANSPORTS: Final[frozenset[str]] = frozenset({"stdio", "streamable-http"})

_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\$\{(PLUGIN_ROOT|PLUGIN_DATA)\}")

#: Headers the client generates to implement HTTP or MCP. §7.2.1 gives them
#: precedence over a configured header of the same name, so a configured one is
#: dropped: left in, httpx would send it as written -- a forged ``Host``, or a
#: ``Content-Length`` that contradicts the body.
_CLIENT_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "accept",
        "connection",
        "content-length",
        "content-type",
        "host",
        "keep-alive",
        "last-event-id",
        "mcp-protocol-version",
        "mcp-session-id",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

#: RFC 9110 token characters, the legal alphabet for a header field name.
_TOKEN_CHARS: Final[frozenset[str]] = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


#: Parent of the ``PLUGIN_DATA`` directories (§9.1). Outside ``plugins/``, whose
#: every child is loaded as a plugin. Bound at import, like the other defaults
#: here, so a later persistence-dir change needs ``plugin_data_root``.
DEFAULT_PLUGIN_DATA_DIR = get_user_persistence_dir() / "plugin-data"


class MCPConfigError(ValueError):
    """An ``mcp.json`` document, or one server entry in it, is invalid.

    Never fatal to the plugin: the caller turns it into a disabled MCP component
    or a skipped server entry, per §7.2.2.
    """


def get_plugin_data_dir(plugin_root: Path, *, data_root: Path | None = None) -> Path:
    """Return a plugin's ``PLUGIN_DATA`` directory, without creating it.

    Keyed by resolved root, not name: roots are stable across updates, and a
    same-named plugin elsewhere must not share the data.
    """
    return get_cache_path(
        plugin_root.resolve().as_posix(), data_root or DEFAULT_PLUGIN_DATA_DIR
    )


def load_mcp_servers(
    plugin_dir: Path,
    *,
    plugin_root: Path,
    plugin_data: Path,
) -> dict[str, MCPServer]:
    """Load ``mcp.json`` from ``plugin_dir``.

    Args:
        plugin_dir: The plugin directory as given (used in messages).
        plugin_root: The filesystem-resolved plugin root — ``${PLUGIN_ROOT}``,
            and the containment boundary for package paths.
        plugin_data: The plugin's persistent data directory — ``${PLUGIN_DATA}``.

    Returns:
        The servers that loaded. Empty when ``mcp.json`` is absent (§6.2) or the
        document as a whole is invalid (§7.2.2 rule 2).
    """
    mcp_path = plugin_dir / MCP_FILE
    if not mcp_path.is_file() or not resolves_within(mcp_path, plugin_root):
        return {}

    try:
        entries = _read_document(mcp_path)
    except MCPConfigError as e:
        logger.warning("Disabling MCP for %s: %s", plugin_dir, e)
        return {}

    servers: dict[str, MCPServer] = {}
    for name, entry in entries.items():
        try:
            servers[name] = _load_server(entry, plugin_root, plugin_data)
        except MCPConfigError as e:
            logger.warning("Skipping MCP server %r in %s: %s", name, mcp_path, e)

    if servers:
        logger.info("Loaded %d MCP server(s) from %s", len(servers), mcp_path)
    return servers


def _read_document(mcp_path: Path) -> dict[str, Any]:
    """Validate the top-level document and return its ``mcpServers`` map.

    Only the top level is checked here. Entries are validated one at a time in
    :func:`_load_server`, so a single malformed entry cannot disable MCP for the
    whole plugin.
    """
    try:
        # utf-8-sig: tolerate a leading BOM, which RFC 8259 lets us ignore.
        document = json.loads(mcp_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise MCPConfigError(f"invalid JSON in {mcp_path}: {e}") from e
    except (OSError, UnicodeDecodeError) as e:
        raise MCPConfigError(f"failed to read {mcp_path}: {e}") from e

    try:
        Draft202012Validator(_top_level_schema()).validate(document)
    except JSONSchemaValidationError as e:
        raise MCPConfigError(f"invalid {MCP_FILE}: {e.message}") from e

    # The declared version is pinned by the schema's ``$schema`` const, and the
    # manifest is pinned to the same 1.0.0 release, so §7.2.2's "targets a
    # different version than plugin.json" case cannot arise while we support
    # exactly one version.
    return document["mcpServers"]


def _load_server(entry: Any, plugin_root: Path, plugin_data: Path) -> MCPServer:
    """Validate one server entry and map it onto an ``MCPServer``."""
    try:
        _server_validator().validate(entry)
    except JSONSchemaValidationError as e:
        raise MCPConfigError(e.message) from e

    transport = entry["type"]
    if transport not in SUPPORTED_TRANSPORTS:
        raise MCPConfigError(f"unsupported transport {transport!r}")

    fields = (
        _stdio_fields(entry, plugin_root, plugin_data)
        if transport == "stdio"
        else _remote_fields(entry)
    )
    try:
        server = MCPServer.model_validate(fields)
    except ValidationError as e:  # pragma: no cover - the schema constrains this
        raise MCPConfigError(str(e)) from e

    if transport == "stdio":
        # §9.1: it must exist and be writable before the subprocess launches.
        # Created only once the entry is known to be a usable stdio server, so
        # a remote-only, invalid or rejected entry leaves no trace on disk.
        try:
            plugin_data.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise MCPConfigError(f"cannot create PLUGIN_DATA {plugin_data}: {e}") from e

    # Fully expanded already; §9.2 forbids expanding anything else.
    return server.as_literal()


def _stdio_fields(
    entry: dict[str, Any], plugin_root: Path, plugin_data: Path
) -> dict[str, Any]:
    """Map a stdio entry: resolve the command, expand, contain the cwd."""
    expand = _expander(plugin_root, plugin_data)

    env = {key: expand(value) for key, value in entry.get("env", {}).items()}
    # §9.1: ours are set last and a plugin cannot override them. The schema
    # already rejects an entry that declares either name itself.
    env |= {"PLUGIN_ROOT": str(plugin_root), "PLUGIN_DATA": str(plugin_data)}

    cwd = entry.get("cwd")
    fields = {
        "transport": "stdio",
        # Never expanded (§9.2), and one token, never a shell string.
        "command": _resolve_command(entry["command"], plugin_root),
        "args": [expand(arg) for arg in entry.get("args", [])],
        "env": {key: SecretStr(value) for key, value in env.items()},
        # §7.2.1: the plugin root is the default working directory.
        "cwd": str(
            _resolve_cwd(cwd, expand, plugin_root, plugin_data)
            if cwd is not None
            else plugin_root
        ),
    }
    return fields


def _remote_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Map a ``streamable-http`` entry: validate the URL and literal headers."""
    return {
        "transport": entry["type"],
        "url": _validate_url(entry["url"]),
        "headers": {
            name: SecretStr(value)
            for name, value in _validate_headers(
                entry.get("headers", {}), entry["url"]
            ).items()
        },
    }


def _expander(plugin_root: Path, plugin_data: Path) -> Callable[[str], str]:
    """Return the §9.2 expander: single-pass, non-recursive, two placeholders.

    One ``re.sub`` pass, so text a replacement introduces is never rescanned --
    not even when a path itself contains ``${PLUGIN_DATA}`` -- and any other
    placeholder-like text is left literal by construction.
    """
    values = {"PLUGIN_ROOT": str(plugin_root), "PLUGIN_DATA": str(plugin_data)}

    def expand(value: str) -> str:
        return _PLACEHOLDER.sub(lambda match: values[match.group(1)], value)

    return expand


def _resolve_command(command: str, plugin_root: Path) -> str:
    """Resolve ``command``: a bare executable name, or a ``./`` package path."""
    if not command.startswith("./"):
        # A bare name goes to the platform's executable search. Anything else --
        # absolute, ``../``, or a bare relative path -- is not a legal form.
        if "/" in command or "\\" in command:
            raise MCPConfigError(
                f"command {command!r} must be a bare executable name or a "
                "plugin-relative path beginning with './'"
            )
        return command
    return str(_contained(plugin_root / command[2:], plugin_root, "command"))


def _resolve_cwd(
    cwd: str,
    expand: Callable[[str], str],
    plugin_root: Path,
    plugin_data: Path,
) -> Path:
    """Resolve an explicit ``cwd``, keeping it inside the root it is written for.

    The schema constrains the three legal prefixes; what it cannot check is where
    they land once expanded and resolved, which is what this enforces.
    """
    if cwd.startswith("./"):
        return _contained(plugin_root / expand(cwd[2:]), plugin_root, "cwd")
    root = plugin_data if cwd.startswith("${PLUGIN_DATA}") else plugin_root
    return _contained(Path(expand(cwd)), root, "cwd")


def _contained(path: Path, root: Path, field: str) -> Path:
    """Resolve ``path`` and require it to stay within ``root`` (§4.1)."""
    if not resolves_within(path, root):
        raise MCPConfigError(f"{field} {str(path)!r} escapes {root}")
    return path.resolve()


def _validate_url(url: str) -> str:
    """Enforce §7.2.1's remote endpoint rules."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise MCPConfigError(f"url {url!r} must be an absolute http(s) URL")
    # Checked on the raw text: urlsplit reports an empty user ("https://@host")
    # or fragment ("https://host/#") as falsy, yet both are present.
    if "@" in parts.netloc:
        raise MCPConfigError(f"url {url!r} must not contain user information")
    if "#" in url:
        raise MCPConfigError(f"url {url!r} must not contain a fragment")
    if parts.scheme == "http" and not _is_loopback(parts.hostname):
        raise MCPConfigError(f"url {url!r} must use https: {parts.hostname} is remote")
    return url


def _is_loopback(host: str) -> bool:
    # Exactly ``localhost`` or an IP literal in a loopback range. A name that
    # merely resolves to one -- or merely starts with "127." -- does not qualify.
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_headers(headers: dict[str, str], url: str) -> dict[str, str]:
    """Enforce §7.2.1's header rules and drop headers the client generates."""
    seen: set[str] = set()
    for name, value in headers.items():
        if not name or not set(name) <= _TOKEN_CHARS:
            raise MCPConfigError(f"header name {name!r} is not a valid HTTP token")
        if name.lower() in seen:
            raise MCPConfigError(f"header {name!r} is declared more than once")
        if not all(char in " \t" or 0x21 <= ord(char) <= 0x7E for char in value):
            raise MCPConfigError(
                f"header {name!r} has a value that is not a field value"
            )
        seen.add(name.lower())

    dropped = sorted(name for name in headers if name.lower() in _CLIENT_HEADERS)
    if dropped:
        logger.warning(
            "Ignoring client-generated header(s) %s configured for %s",
            ", ".join(dropped),
            url,
        )
    return {k: v for k, v in headers.items() if k.lower() not in _CLIENT_HEADERS}


@cache
def _top_level_schema() -> dict[str, Any]:
    """The vendored schema with per-server validation removed.

    Derived from the vendored document rather than restated, so the closed set of
    top-level fields and the ``$schema`` const stay single-sourced.
    """
    schema = _load_schema(_MCP_SCHEMA_FILE)
    top_level = {key: value for key, value in schema.items() if key != "$defs"}
    top_level["properties"] = dict(
        top_level["properties"], mcpServers={"type": "object"}
    )
    return top_level


@cache
def _server_validator() -> Draft202012Validator:
    """Validate one entry against the ``#/$defs/server`` the spec exposes."""
    return Draft202012Validator(
        {"$ref": "#/$defs/server", "$defs": _load_schema(_MCP_SCHEMA_FILE)["$defs"]}
    )
