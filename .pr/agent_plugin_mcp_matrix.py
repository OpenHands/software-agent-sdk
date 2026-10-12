"""#5276: Agent Plugins remote MCP servers x {header, no header} x {success, failure}.

Loads a real Agent Plugins package (plugin.json + mcp.json) through the SDK's
AgentPluginsFormat, so every server has literal_values=True and takes the
package path in _prepare_mcp_config, then connects with create_mcp_tools().

Run from the SDK repo:
    uv run python .pr/agent_plugin_mcp_matrix.py
"""

import json
import tempfile
from pathlib import Path

import httpx

from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.plugin.format.agent_plugins import AgentPluginsFormat


SUCCESS_URL = "https://huggingface.co/mcp"
FAILURE_URL = "https://gitlab.com/api/v4/mcp"
HEADER = {"X-Plugin-Test": "1"}

CASES = {
    "header-success": {
        "type": "streamable-http",
        "url": SUCCESS_URL,
        "headers": HEADER,
    },
    "noheader-success": {"type": "streamable-http", "url": SUCCESS_URL},
    "header-failure": {
        "type": "streamable-http",
        "url": FAILURE_URL,
        "headers": HEADER,
    },
    "noheader-failure": {"type": "streamable-http", "url": FAILURE_URL},
}


def write_package(root: Path) -> None:
    (root / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "mcp-matrix",
                "version": "1.0.0",
                "description": "#5276 Agent Plugins MCP matrix.",
            }
        ),
        encoding="utf-8",
    )
    (root / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                "mcpServers": CASES,
            }
        ),
        encoding="utf-8",
    )


def run_case(name, server) -> None:
    headers = sorted(server.headers) if server.headers else "none"
    print(
        f"== {name}: url={server.url} headers={headers} "
        f"literal_values={server.literal_values}"
    )
    try:
        with create_mcp_tools({name: server}, timeout=30) as client:
            print(f"   OK: {len(client.tools)} tools")
    except httpx.HTTPStatusError as exc:
        try:
            body = exc.response.text
        except httpx.ResponseNotRead:
            body = "<ResponseNotRead>"
        print(f"   HTTPStatusError {exc.response.status_code}; body={body!r}")
    except Exception as exc:  # noqa: BLE001 - report anything else verbatim
        print(f"   {type(exc).__name__}: {exc}")


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "mcp-matrix"
    root.mkdir()
    write_package(root)
    fmt = AgentPluginsFormat(plugin_data_root=Path(tmp) / "plugin-data")
    servers = fmt.load_mcp_config(root)
    missing = set(CASES) - set(servers)
    if missing:
        print(f"!! skipped by the loader: {sorted(missing)}")
    for name in CASES:
        if name in servers:
            run_case(name, servers[name])
