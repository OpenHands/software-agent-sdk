"""Helpers for the tool families' programs (S14 to S17).

A program builds an agent with tools, drives it with a scripted model through
a real conversation, and reports what each tool call produced. Scripts may
use `{ws}` in string arguments for the conversation's workspace directory,
since the file tools take absolute paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sdk_verify import make_llm, run_paths


def workspace() -> Path:
    return run_paths()["workspace"]


def expand(script: list[dict[str, Any]], ws: Path | str | None = None) -> list[dict]:
    """Replace `{ws}` in every string of the script with the workspace path."""
    text = json.dumps(script).replace("{ws}", str(ws or workspace()))
    return json.loads(text)


def load_script(name: str, ws: Path | str | None = None) -> list[dict[str, Any]]:
    """A script file next to the calling program, `{ws}` expanded."""
    here = Path(__file__).resolve().parent.parent
    return expand(json.loads((here / name).read_text()), ws)


def converse(
    tools: list[Any],
    script: list[dict[str, Any]],
    text: str = "Do the task.",
    *,
    secrets: dict[str, str] | None = None,
    agent_kwargs: dict[str, Any] | None = None,
    conversation_kwargs: dict[str, Any] | None = None,
    ws: Path | str | None = None,
    persist: bool = True,
    run: bool = True,
    llm: Any = None,
    conversation_class: Any = None,
) -> Any:
    """Build Agent + Conversation the way the examples do and run one turn.

    `conversation_class` (e.g. `LocalConversation`) replaces the
    `Conversation` factory for options only it takes. Returns the open
    conversation; the caller closes it."""
    from openhands.sdk import Agent, Conversation

    agent = Agent(
        llm=llm if llm is not None else make_llm(expand(script, ws)),
        tools=tools,
        **(agent_kwargs or {}),
    )
    options: dict[str, Any] = {"visualizer": None}
    if persist:
        options["persistence_dir"] = str(run_paths()["conversations"])
    options.update(conversation_kwargs or {})
    conversation = (conversation_class or Conversation)(
        agent=agent, workspace=str(ws or workspace()), **options
    )
    if secrets:
        conversation.update_secrets(secrets)
    if run:
        conversation.send_message(text)
        conversation.run()
    return conversation


def content_text(observation: Any) -> str:
    """What the model sees from an observation, text parts joined."""
    parts = []
    for item in observation.to_llm_content:
        parts.append(getattr(item, "text", "") or "")
    return "\n".join(parts)


def tool_results(conversation: Any, clip: int = 3000) -> list[dict[str, Any]]:
    """Every tool result in order: observations, rejections and agent errors."""
    from openhands.sdk.event import AgentErrorEvent, ObservationEvent

    rows: list[dict[str, Any]] = []
    for event in conversation.state.events:
        if isinstance(event, ObservationEvent):
            obs = event.observation
            text = content_text(obs)
            row: dict[str, Any] = {
                "tool": event.tool_name,
                "kind": type(obs).__name__,
                "is_error": bool(getattr(obs, "is_error", False)),
                "text": text if len(text) <= clip else text[:clip] + "…",
                "text_len": len(text),
                "content_types": [type(c).__name__ for c in obs.to_llm_content],
            }
            metadata = getattr(obs, "metadata", None)
            if metadata is not None and hasattr(metadata, "exit_code"):
                row["exit_code"] = getattr(obs, "exit_code", None)
                row["meta_exit_code"] = metadata.exit_code
                row["cwd"] = metadata.working_dir
                row["username"] = metadata.username
                row["py_interpreter"] = metadata.py_interpreter_path
                row["timeout"] = getattr(obs, "timeout", None)
            rows.append(row)
        elif isinstance(event, AgentErrorEvent):
            rows.append(
                {
                    "tool": event.tool_name,
                    "kind": "AgentErrorEvent",
                    "is_error": True,
                    "text": event.error[:clip],
                    "text_len": len(event.error),
                    "content_types": [],
                }
            )
    return rows


def by_tool(rows: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [r for r in rows if r["tool"] == name]
