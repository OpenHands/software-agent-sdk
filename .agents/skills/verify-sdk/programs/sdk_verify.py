"""Shared helpers for verify-sdk programs and the control-sdk CLI.

A program is a short script that uses the public SDK the way a user's program
does and prints one JSON object as its last line of output; the recipe checks
fields in it. `control-sdk exec` runs it inside an isolated run (private HOME,
persistence directory and workspace) with this directory on PYTHONPATH:

    from sdk_verify import emit, make_llm, run_paths

    paths = run_paths()
    llm = make_llm(script=[{"text": "hello"}])  # scripted, no key needed
    ...
    emit({"ok": True, "events": 3})

`make_llm()` without a script returns the run's real model (`control-sdk new
--llm deepseek-flash`), reading the key from DEEPSEEK_API_KEY at call time. A
missing key exits 3, which `control-sdk` and `sdk-map run` report as blocked.
Keys are never printed or written.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


BLOCKED_EXIT = 3

# Real models a run can use. The key is read from the environment, never stored.
MODELS: dict[str, dict[str, str]] = {
    "deepseek-flash": {"model": "deepseek/deepseek-flash", "env": "DEEPSEEK_API_KEY"},
    "deepseek-pro": {"model": "deepseek/deepseek-v4-pro", "env": "DEEPSEEK_API_KEY"},
}


def emit(result: dict[str, Any]) -> None:
    """Print the program's result as the last line of output."""
    sys.stdout.flush()
    print(json.dumps(result, default=str))


def blocked(reason: str) -> None:
    emit({"ok": False, "blocked": reason})
    raise SystemExit(BLOCKED_EXIT)


def run_paths() -> dict[str, Path]:
    """The current run's directories (set by `control-sdk exec`)."""
    root = os.environ.get("SDK_VERIFY_RUN")
    if not root:
        raise SystemExit(
            "SDK_VERIFY_RUN is not set; run programs through `control-sdk exec`"
        )
    base = Path(root)
    return {
        "run": base,
        "home": base / "home",
        # OH_PERSISTENCE_DIR: what ~/.openhands holds for a real user.
        "openhands": base / "openhands",
        # Conversation(persistence_dir=...): one directory per conversation.
        "conversations": base / "conversations",
        "workspace": base / "workspace",
    }


def run_config() -> dict[str, Any]:
    return json.loads((run_paths()["run"] / "run.json").read_text())


# --------------------------------------------------------------------------
# Scripted model
#
# A script is a list of steps, one per LLM call:
#   {"text": "..."}                                   an assistant message
#   {"tool": "terminal", "args": {"command": "ls"}}   one tool call
#   {"tools": [{"tool": ..., "args": ...}, ...]}      parallel tool calls
#   {"finish": "done"}                                the finish tool
#   {"error": "LLMContextWindowExceedError"}          raise this LLM error
# --------------------------------------------------------------------------


def script_messages(script: list[dict[str, Any]]) -> list[Any]:
    from openhands.sdk.llm import (
        Message,
        MessageToolCall,
        TextContent,
        exceptions as llm_errors,
    )

    items: list[Any] = []
    for n, step in enumerate(script):
        if "error" in step:
            error = getattr(llm_errors, step["error"])
            items.append(error(step.get("message", "scripted error")))
            continue
        calls = []
        if "finish" in step:
            calls = [{"tool": "finish", "args": {"message": step["finish"]}}]
        elif "tool" in step:
            calls = [step]
        elif "tools" in step:
            calls = step["tools"]
        tool_calls = [
            MessageToolCall(
                id=f"call_{n}_{i}",
                name=call["tool"],
                arguments=json.dumps(call.get("args", {})),
                origin="completion",
            )
            for i, call in enumerate(calls)
        ]
        items.append(
            Message(
                role="assistant",
                content=[TextContent(text=step.get("text", ""))],
                tool_calls=tool_calls or None,
            )
        )
    return items


def make_llm(
    script: list[dict[str, Any]] | None = None,
    *,
    usage_id: str = "agent",
    model: str | None = None,
    **kwargs: Any,
) -> Any:
    """A scripted TestLLM when `script` is given, else the run's real model."""
    if script is not None:
        from openhands.sdk.testing import TestLLM

        return TestLLM.from_messages(
            script_messages(script), usage_id=usage_id, **kwargs
        )
    from pydantic import SecretStr

    from openhands.sdk import LLM

    name = model or run_config().get("llm", "scripted")
    if name not in MODELS:
        raise SystemExit(
            f"run model is {name!r}: pass a script, or create the run with"
            f" `control-sdk new --llm {next(iter(MODELS))}`"
        )
    spec = MODELS[name]
    key = os.environ.get(spec["env"])
    if not key:
        blocked(f"set {spec['env']} for {name}")
    return LLM(
        model=spec["model"], api_key=SecretStr(key or ""), usage_id=usage_id, **kwargs
    )


def has_model_key() -> bool:
    name = run_config().get("llm", "scripted")
    return name in MODELS and bool(os.environ.get(MODELS[name]["env"]))


# --------------------------------------------------------------------------
# Conversations created with `control-sdk conversation new`
# --------------------------------------------------------------------------

TOOL_MODULES = {
    "terminal": "openhands.tools.terminal",
    "file_editor": "openhands.tools.file_editor",
    "task_tracker": "openhands.tools.task_tracker",
    "browser_use": "openhands.tools.browser_use",
    "glob": "openhands.tools.glob",
    "grep": "openhands.tools.grep",
    "apply_patch": "openhands.tools.apply_patch",
    "planning_file_editor": "openhands.tools.planning_file_editor",
}


def import_tools(names: list[str]) -> None:
    """Register tool classes, as a program does by importing them; restoring
    a saved SystemPromptEvent needs them registered."""
    import importlib

    for name in names:
        if name in TOOL_MODULES:
            importlib.import_module(TOOL_MODULES[name])


def conversation_record(cid: str) -> dict[str, Any]:
    path = run_paths()["run"] / "state" / f"{cid}.json"
    if not path.is_file():
        raise SystemExit(f"unknown conversation {cid} in this run")
    return json.loads(path.read_text())


def conversation_dir(cid: str) -> Path:
    """Where the SDK persists this conversation (base_state.json, events/)."""
    return run_paths()["conversations"] / cid.replace("-", "")


def open_conversation(record: dict[str, Any], *, llm: Any = None, **kwargs: Any) -> Any:
    """Reopen a persisted conversation the way a program resumes one."""
    import uuid

    from openhands.sdk import Agent, Conversation, Tool

    import_tools(record["tools"])
    if llm is None:
        script = record.get("script")
        llm = make_llm(None if script is None else script[record.get("used", 0) :])
    agent = Agent(llm=llm, tools=[Tool(name=n) for n in record["tools"]])
    options: dict[str, Any] = {
        "max_iteration_per_run": record.get("max_iterations", 50),
        "visualizer": None,
        "delete_on_close": False,
    }
    options.update(kwargs)
    return Conversation(
        agent=agent,
        workspace=str(run_paths()["workspace"]),
        persistence_dir=str(run_paths()["conversations"]),
        conversation_id=uuid.UUID(record["id"]),
        **options,
    )


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------


def event_summary(index: int, event: Any) -> dict[str, Any]:
    """A small, stable view of an event for checks."""
    row: dict[str, Any] = {
        "index": index,
        "kind": type(event).__name__,
        "source": getattr(event, "source", None),
        "id": getattr(event, "id", None),
    }
    action = getattr(event, "action", None)
    if action is not None:
        row["tool"] = getattr(event, "tool_name", None)
        row["action"] = action.model_dump(exclude_none=True, mode="json")
    observation = getattr(event, "observation", None)
    if observation is not None:
        row["tool"] = getattr(event, "tool_name", None)
        row["text"] = _clip(_observation_text(observation))
        row["is_error"] = bool(getattr(observation, "is_error", False))
    message = getattr(event, "llm_message", None)
    if message is not None and type(event).__name__ == "MessageEvent":
        row["role"] = message.role
        row["text"] = _clip(
            "".join(getattr(c, "text", "") for c in message.content or [])
        )
    classification = getattr(event, "classification", None)
    if classification is not None:
        row["failure_kind"] = str(classification.kind.value)
    for field in ("error", "detail", "code", "reason", "key", "value"):
        value = getattr(event, field, None)
        if isinstance(value, str | int | float | bool):
            row[field] = _clip(str(value)) if isinstance(value, str) else value
    return row


def _observation_text(observation: Any) -> str:
    content = getattr(observation, "content", None)
    if isinstance(content, list):
        return "".join(getattr(c, "text", "") for c in content)
    return str(getattr(observation, "text", "") or content or "")


def _clip(text: str, limit: int = 400) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
