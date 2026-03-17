"""Codebase search tools powered by Morph's WarpGrep SDK.

Registers ``codebase_search`` and ``github_codebase_search`` as native
OpenHands tools.  Each tool calls ``@morphllm/morphsdk`` via a small
Node.js bridge script (``bridge.js``), which handles the multi-turn
WarpGrep agent loop internally and returns aggregated results.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from openhands.sdk.logger import get_logger
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)

if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)

_BRIDGE_SCRIPT = Path(__file__).parent / "bridge.js"

_CODEBASE_SEARCH_DESCRIPTION = """\
Search the local codebase using natural language. This tool uses a \
specialised code-search sub-agent (WarpGrep) that runs ripgrep and file \
reads internally, then returns the most relevant code snippets.

Pass a natural-language question — do NOT pass regex or symbol-only queries.

Good: "Where does authentication get handled?"
Bad:  "auth()" or "grep -r auth"
"""

_GITHUB_CODEBASE_SEARCH_DESCRIPTION = """\
Search a public GitHub repository using natural language. Provide either \
a full GitHub URL or an owner/repo shorthand (e.g. "expressjs/express"). \
The tool clones and searches the repo remotely — no local checkout needed.

Pass a natural-language question — do NOT pass regex or symbol-only queries.
"""


# ── Actions ─────────────────────────────────────────────────────────────


class CodebaseSearchAction(Action):
    """Search a local repository with a natural-language query."""

    search_string: str = Field(
        description=(
            "Natural-language question about the code you want to understand. "
            "Good: 'Where does auth get handled?' "
            "Bad: 'auth()'"
        ),
    )
    repo_path: str = Field(
        description="Absolute path to the repository root to search.",
    )


class GitHubCodebaseSearchAction(Action):
    """Search a public GitHub repository with a natural-language query."""

    search_string: str = Field(
        description=(
            "Natural-language question about the code you want to understand. "
            "Good: 'Where does auth get handled?' "
            "Bad: 'auth()'"
        ),
    )
    github_url: str | None = Field(
        default=None,
        description=(
            "Full GitHub URL (e.g. 'https://github.com/expressjs/express'). "
            "Provide either github_url or owner_repo."
        ),
    )
    owner_repo: str | None = Field(
        default=None,
        description=(
            "Repository shorthand (e.g. 'expressjs/express'). "
            "Provide either github_url or owner_repo."
        ),
    )
    branch: str | None = Field(
        default=None,
        description="Branch to search. Defaults to the repo's default branch.",
    )


# ── Observations ────────────────────────────────────────────────────────


class CodebaseSearchObservation(Observation):
    """Results from a codebase search."""

    pass  # Uses base Observation's text field


# ── Executors ───────────────────────────────────────────────────────────


def _validate_api_key(api_key: str | None) -> str:
    """Return a validated MORPH_API_KEY or raise with a helpful message."""
    key = api_key or os.environ.get("MORPH_API_KEY")
    if not key:
        raise ValueError(
            "MORPH_API_KEY is required for codebase_search.\n"
            "Set it as an environment variable:\n"
            "  export MORPH_API_KEY=sk-morph-...\n"
            "Or pass it in Tool params:\n"
            "  Tool(name='codebase_search', params={'api_key': 'sk-morph-...'})\n\n"
            "Get your key at https://morphllm.com/dashboard/api-keys"
        )
    return key


def _run_bridge(payload: dict, api_key: str) -> dict:
    """Call the Node.js bridge script and return parsed JSON."""
    env = {**os.environ, "MORPH_API_KEY": api_key}

    try:
        proc = subprocess.run(
            ["node", str(_BRIDGE_SCRIPT)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except FileNotFoundError:
        raise ValueError(
            "Node.js is required for codebase_search but 'node' was not found.\n"
            "Install Node.js 18+ from https://nodejs.org/"
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Search timed out after 120 seconds."}

    if proc.returncode != 0 and not proc.stdout.strip():
        stderr = proc.stderr.strip()
        # Check for missing SDK
        if "Cannot find module" in stderr or "MODULE_NOT_FOUND" in stderr:
            return {
                "success": False,
                "error": (
                    "@morphllm/morphsdk is not installed. Run:\n"
                    "  npm install -g @morphllm/morphsdk"
                ),
            }
        return {"success": False, "error": stderr[:500] if stderr else "Bridge process failed."}

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"success": False, "error": f"Invalid JSON from bridge: {proc.stdout[:200]}"}


def _format_result(result: dict) -> str:
    """Format a WarpGrep result dict into readable text for the LLM."""
    if not result.get("success"):
        return f"Search failed: {result.get('error', 'Unknown error')}"

    contexts = result.get("contexts", [])
    if not contexts:
        return "No relevant code found."

    parts: list[str] = []
    if result.get("summary"):
        parts.append(result["summary"])
        parts.append("")

    for ctx in contexts:
        file_path = ctx.get("file", "unknown")
        content = ctx.get("content", "")
        if content:
            parts.append(f"--- {file_path} ---")
            parts.append(content)
            parts.append("")

    return "\n".join(parts).strip()


class CodebaseSearchExecutor(ToolExecutor[CodebaseSearchAction, CodebaseSearchObservation]):
    """Execute local codebase search via the Morph SDK."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def __call__(
        self,
        action: CodebaseSearchAction,
        conversation: LocalConversation | None = None,
    ) -> CodebaseSearchObservation:
        result = _run_bridge(
            {"type": "local", "query": action.search_string, "repo_path": action.repo_path},
            self._api_key,
        )
        return CodebaseSearchObservation.from_text(text=_format_result(result))


class GitHubCodebaseSearchExecutor(ToolExecutor[GitHubCodebaseSearchAction, CodebaseSearchObservation]):
    """Execute GitHub codebase search via the Morph SDK."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def __call__(
        self,
        action: GitHubCodebaseSearchAction,
        conversation: LocalConversation | None = None,
    ) -> CodebaseSearchObservation:
        result = _run_bridge(
            {
                "type": "github",
                "query": action.search_string,
                "github_url": action.github_url,
                "owner_repo": action.owner_repo,
                "branch": action.branch,
            },
            self._api_key,
        )
        return CodebaseSearchObservation.from_text(text=_format_result(result))


# ── Tool Definitions ────────────────────────────────────────────────────


class CodebaseSearchTool(ToolDefinition[CodebaseSearchAction, CodebaseSearchObservation]):
    """Local codebase search powered by Morph WarpGrep."""

    @classmethod
    def create(
        cls,
        conv_state: ConversationState,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> Sequence[CodebaseSearchTool]:
        key = _validate_api_key(api_key)
        working_dir = conv_state.workspace.working_dir
        description = (
            f"{_CODEBASE_SEARCH_DESCRIPTION}\n"
            f"Your current working directory is: {working_dir}"
        )
        return [
            cls(
                description=description,
                action_type=CodebaseSearchAction,
                observation_type=CodebaseSearchObservation,
                executor=CodebaseSearchExecutor(api_key=key),
                annotations=ToolAnnotations(
                    title="codebase_search",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
            )
        ]


class GitHubCodebaseSearchTool(ToolDefinition[GitHubCodebaseSearchAction, CodebaseSearchObservation]):
    """GitHub codebase search powered by Morph WarpGrep."""

    name = "github_codebase_search"  # override auto-naming of "git_hub_codebase_search"

    @classmethod
    def create(
        cls,
        conv_state: ConversationState,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> Sequence[GitHubCodebaseSearchTool]:
        key = _validate_api_key(api_key)
        return [
            cls(
                description=_GITHUB_CODEBASE_SEARCH_DESCRIPTION,
                action_type=GitHubCodebaseSearchAction,
                observation_type=CodebaseSearchObservation,
                executor=GitHubCodebaseSearchExecutor(api_key=key),
                annotations=ToolAnnotations(
                    title="github_codebase_search",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
            )
        ]


# ── Registration ────────────────────────────────────────────────────────


def register_codebase_search_tools() -> None:
    """Register ``codebase_search`` and ``github_codebase_search`` tools.

    Call this once before creating an Agent that uses these tools.
    Registration is explicit (not at import time) to avoid import-time
    side-effects.
    """
    register_tool(CodebaseSearchTool.name, CodebaseSearchTool)
    register_tool(GitHubCodebaseSearchTool.name, GitHubCodebaseSearchTool)
