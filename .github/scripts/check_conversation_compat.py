#!/usr/bin/env python3
"""Resume a conversation persisted by the previous openhands-sdk release.

The previous PyPI release is installed with dependencies as of its release
date, persists a conversation with a stdio MCP server attached, and the
current checkout must resume it. Dependency pins can change the persisted
format without any SDK code change (#5251), so this runs on every PR.

    uv run python .github/scripts/check_conversation_compat.py
    uv run python .github/scripts/check_conversation_compat.py \\
        --sdk-version 1.49.1 --save-to tests/fixtures/conversations/v1_49_1_mcp
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH_PLACEHOLDER = "/fixture"

_spec = importlib.util.spec_from_file_location(
    "check_persisted_settings_compat",
    Path(__file__).with_name("check_persisted_settings_compat.py"),
)
assert _spec is not None and _spec.loader is not None
settings_compat = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = settings_compat
_spec.loader.exec_module(settings_compat)

_MCP_SERVER = '''
from fastmcp import FastMCP

mcp = FastMCP("files")


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def read_file(path: str, mime_type: str = "text/plain") -> str:
    """Read a file."""
    return ""


mcp.run()
'''

_WRITER = """
import sys
import uuid
from pydantic import SecretStr
from openhands.sdk import LLM, Agent, Conversation

root, server, conversation_id = sys.argv[1:4]
agent = Agent(
    llm=LLM(model="fixture-model", api_key=SecretStr("fixture-key"), usage_id="agent"),
    tools=[],
    mcp_config={"files": {"command": sys.executable, "args": [server]}},
)
conversation = Conversation(
    agent=agent,
    workspace=root + "/workspace",
    persistence_dir=root + "/conversations",
    conversation_id=uuid.UUID(conversation_id),
    visualizer=None,
)
conversation.send_message("hello")
conversation.close()
"""


class ConversationCompatError(RuntimeError):
    """Raised when a persisted conversation no longer resumes."""


def write_baseline_conversation(root: Path, sdk_version: str) -> Path:
    """Persist a conversation with ``sdk_version``; return its directory."""
    cutoff = settings_compat.get_pypi_release_cutoff("openhands-sdk", sdk_version)
    venv = root / "venv"
    python = settings_compat._venv_python(venv)
    server = root / "mcp_server.py"
    server.write_text(_MCP_SERVER)
    conversation_id = str(uuid.uuid4())
    try:
        settings_compat._uv_run(["uv", "venv", str(venv), "--python", sys.executable])
        settings_compat._uv_run(
            ["uv", "pip", "install", "--python", str(python), "--quiet"]
            + ["--exclude-newer", cutoff, f"openhands-sdk=={sdk_version}"]
        )
        settings_compat._uv_run(
            [str(python), "-c", _WRITER, str(root), str(server), conversation_id]
        )
    except subprocess.CalledProcessError as exc:
        output = ((exc.stdout or "") + "\n" + (exc.stderr or "")).strip()[-1000:]
        raise ConversationCompatError(
            f"openhands-sdk=={sdk_version} (exclude-newer={cutoff}) failed to "
            f"persist a conversation: {output}"
        ) from exc
    return root / "conversations" / uuid.UUID(conversation_id).hex


def resume_conversation(conversation_dir: Path, workspace: Path) -> int:
    """Resume with the current checkout; return the number of events loaded."""
    from openhands.sdk.conversation.impl.local_conversation import (
        LocalConversation,
    )
    from openhands.sdk.event import SystemPromptEvent
    from openhands.sdk.mcp.tool import MCPToolDefinition

    on_disk = len(list((conversation_dir / "events").glob("event-*.json")))
    try:
        conversation = LocalConversation(
            agent=None,
            workspace=str(workspace),
            persistence_dir=str(conversation_dir.parent),
            conversation_id=uuid.UUID(conversation_dir.name),
            visualizer=None,
        )
    except Exception as exc:
        raise ConversationCompatError(f"Resume failed: {exc}") from exc
    try:
        events = list(conversation.state.events)
        mcp_tools = [
            tool
            for event in events
            if isinstance(event, SystemPromptEvent)
            for tool in event.tools
            if isinstance(tool, MCPToolDefinition)
        ]
    finally:
        conversation.close()
    if len(events) != on_disk:
        raise ConversationCompatError(
            f"Loaded {len(events)} of {on_disk} persisted events."
        )
    if [tool.name for tool in mcp_tools] != ["read_file"]:
        raise ConversationCompatError(
            f"Expected the persisted MCP tool read_file, got {mcp_tools!r}."
        )
    return len(events)


def save_fixture(conversation_dir: Path, root: Path, destination: Path) -> None:
    shutil.copytree(
        conversation_dir, destination, ignore=shutil.ignore_patterns(".eventlog*")
    )
    for path in destination.rglob("*.json"):
        text = path.read_text().replace(str(root), FIXTURE_PATH_PLACEHOLDER)
        path.write_text(json.dumps(json.loads(text), indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sdk-version", help="Default: previous PyPI release.")
    parser.add_argument("--save-to", type=Path, help="Store the conversation.")
    args = parser.parse_args()

    sdk_version = args.sdk_version
    if sdk_version is None:
        current = settings_compat.read_version_from_pyproject(
            REPO_ROOT / "openhands-sdk" / "pyproject.toml"
        )
        sdk_version = settings_compat.get_pypi_baseline_version(
            "openhands-sdk", current
        )
        if sdk_version is None:
            print("::warning::No published openhands-sdk baseline; skipping")
            return 0

    with tempfile.TemporaryDirectory(prefix="conversation-compat-") as tmp:
        root = Path(tmp).resolve()
        conversation_dir = write_baseline_conversation(root, sdk_version)
        count = resume_conversation(conversation_dir, root / "workspace")
        if args.save_to is not None:
            save_fixture(conversation_dir, root, args.save_to)
    print(
        f"Resumed a conversation persisted by openhands-sdk=={sdk_version} "
        f"({count} events, MCP tool attached)"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConversationCompatError as exc:
        print(f"::error title=Conversation compatibility::{exc}")
        raise SystemExit(1) from exc
