"""Live DeepSeek V4.1 Flash check for tool-call batch splitting (issue #5354).

Usage (from the SDK checkout, after `make build`; the key is read from the environment):

    uv run python .pr/live_deepseek.py --case 1 --label pr --out /tmp/live.raw.json

Only the first LLM completion is scripted (assistant with reasoning_content and
parallel tool calls, one of which fails SDK argument validation). Every later
completion goes to OpenRouter unmodified. The SDK-level message list and the
raw HTTP request body/response are recorded for each real call.
Output files and console output are raw diagnostics: sanitize provider account IDs,
request IDs, local paths, and prompt content before publishing. The checked-in
JSON records were separately curated; this script does not produce their schema.
"""

import argparse
import copy
import json
import os
import platform
import tempfile
import traceback
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
    Usage,
)
from pydantic import SecretStr

import openhands.sdk.llm.llm as llm_module
from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.llm.utils.model_features import get_features
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool


REASONING = (
    "The user wants the notes file inspected and a task list set up. "
    "I will call both tools in parallel in a single response."
)


def load_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("set OPENROUTER_API_KEY")
    return key


def case_calls(case: str, notes: str) -> list[tuple[str, str, str]]:
    view = json.dumps({"command": "view", "path": notes})
    bad_task = json.dumps(
        {
            "command": "plan",
            "task_list": [
                {"title": "Summarize notes", "notes": "", "status": "pending"}
            ],
        }
    )
    good_task = json.dumps(
        {
            "command": "plan",
            "task_list": [{"title": "Summarize notes", "notes": "", "status": "todo"}],
        }
    )
    malformed = '{"command": "view", "path": ' + notes  # truncated, not JSON
    if case == "1":
        return [
            ("call_00_k3Xq9TtPz1", "task_tracker", bad_task),
            ("call_01_Vb7mR2cLw8", "file_editor", view),
        ]
    if case == "2":
        return [
            ("call_00_Hq4nT6yUe2", "file_editor", malformed),
            ("call_01_Pz8sD1kFa5", "task_tracker", good_task),
        ]
    if case == "3":
        return [
            ("call_00_Aa1bC2dE3f", "file_editor", view),
            ("call_01_Bb2cD3eF4g", "task_tracker", bad_task),
            ("call_02_Cc3dE4fG5h", "task_tracker", json.dumps({"command": "view"})),
        ]
    raise ValueError(case)


def scripted_response(calls: list[tuple[str, str, str]]) -> ModelResponse:
    return ModelResponse(
        id="gen-scripted-first",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="",
                    reasoning_content=REASONING,
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id=cid,
                            type="function",
                            function=Function(name=name, arguments=args),
                        )
                        for cid, name, args in calls
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        created=0,
        model="deepseek/deepseek-v4.1-flash",
        object="chat.completion",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def shape(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        item: dict[str, Any] = {"role": m.get("role")}
        if m.get("tool_calls"):
            item["tool_calls"] = [
                f"{c['id']}:{c['function']['name']}" for c in m["tool_calls"]
            ]
        if m.get("role") == "assistant":
            rc = m.get("reasoning_content")
            item["reasoning_content"] = (
                "absent"
                if rc is None
                else ("empty" if rc == "" else f"{len(rc)} chars")
            )
        if m.get("role") == "tool":
            item["tool_call_id"] = m.get("tool_call_id")
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            item["content_head"] = (content or "")[:160]
        out.append(item)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, choices=["1", "2", "3"])
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", default="openai/deepseek/deepseek-v4.1-flash")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    key = load_key()
    features = get_features(args.model)
    record: dict[str, Any] = {
        "label": args.label,
        "case": args.case,
        "model": args.model,
        "send_reasoning_content": features.send_reasoning_content,
        "python": platform.python_version(),
        "openhands_sdk": version("openhands-sdk"),
        "openhands_tools": version("openhands-tools"),
        "litellm": version("litellm"),
        "pydantic": version("pydantic"),
        "sdk_requests": [],
        "http": [],
    }
    if not features.send_reasoning_content:
        raise SystemExit(f"send_reasoning_content is false for {args.model}")

    real_completion = llm_module.litellm_completion
    n_calls = {"llm": 0}

    def wrapped_completion(**kwargs: Any) -> Any:
        n_calls["llm"] += 1
        msgs = copy.deepcopy(kwargs.get("messages"))
        if n_calls["llm"] == 1:
            record["first_request_shape"] = shape(msgs)
            return scripted_response(calls)
        params = {
            k: v for k, v in kwargs.items() if k not in ("messages", "api_key", "tools")
        }
        record["sdk_requests"].append(
            {
                "index": n_calls["llm"],
                "params": json.loads(json.dumps(params, default=str)),
                "messages": msgs,
                "shape": shape(msgs),
            }
        )
        return real_completion(**kwargs)

    real_send = httpx.Client.send

    def wrapped_send(self: httpx.Client, request: httpx.Request, **kw: Any) -> Any:
        entry: dict[str, Any] = {"url": str(request.url)}
        try:
            entry["body"] = json.loads(request.content)
        except Exception:
            entry["body_raw"] = request.content.decode(errors="replace")[:2000]
        try:
            resp = real_send(self, request, **kw)
        except Exception as e:
            entry["exception"] = repr(e)
            record["http"].append(entry)
            raise
        try:
            resp.read()
            entry["status"] = resp.status_code
            entry["response_text"] = resp.text
        except Exception as e:  # streaming or already consumed
            entry["status"] = resp.status_code
            entry["response_read_error"] = repr(e)
        record["http"].append(entry)
        return resp

    with tempfile.TemporaryDirectory(prefix="oh-pr5355-") as ws:
        notes = str(Path(ws) / "notes.txt")
        Path(notes).write_text("alpha: first item\nbeta: second item\n")
        calls = case_calls(args.case, notes)
        record["scripted_calls"] = [
            {"id": c, "name": n, "arguments": a} for c, n, a in calls
        ]
        llm = LLM(
            model=args.model,
            usage_id="pr5355-live",
            api_key=SecretStr(key),
            base_url="https://openrouter.ai/api/v1",
            api_mode="chat",
            num_retries=0,
            max_output_tokens=512,
            temperature=1.0,
            top_p=0.95,
            reasoning_effort=None,
            litellm_extra_body={
                "reasoning": {"enabled": True, "effort": "low"},
                "provider": {"order": ["DeepSeek"], "allow_fallbacks": False},
            },
        )
        agent = Agent(
            llm=llm,
            tools=[Tool(name=FileEditorTool.name), Tool(name=TaskTrackerTool.name)],
        )
        conv = Conversation(
            agent=agent, workspace=ws, visualizer=None, max_iteration_per_run=2
        )
        try:
            with (
                patch.object(llm_module, "litellm_completion", wrapped_completion),
                patch.object(httpx.Client, "send", wrapped_send),
            ):
                conv.send_message(
                    f"Look at {notes} and set up a task list for summarizing it. "
                    "Then reply with a one-line summary."
                )
                conv.run()
            record["run_result"] = "returned"
        except Exception as e:
            record["run_result"] = "raised"
            record["exception"] = f"{type(e).__name__}: {e}"[:4000]
            record["traceback_tail"] = traceback.format_exc()[-3000:]
        finally:
            record["execution_status"] = str(conv.state.execution_status)
            record["events"] = [
                f"{type(ev).__name__}:{getattr(ev, 'tool_call_id', '') or ''}"
                for ev in conv.state.events
            ]
            conv.close()
    record["llm_calls_total"] = n_calls["llm"]
    record["api_calls_real"] = len(record["http"])
    assert key not in json.dumps(record, default=str), "key leaked into record"
    Path(args.out).write_text(json.dumps(record, indent=2, default=str))
    print(
        json.dumps(
            {
                k: record.get(k)
                for k in (
                    "label",
                    "case",
                    "send_reasoning_content",
                    "openhands_sdk",
                    "llm_calls_total",
                    "api_calls_real",
                    "run_result",
                    "execution_status",
                )
            }
        )
    )
    for h in record["http"]:
        print("HTTP", h.get("status"), (h.get("response_text") or "")[:600])
    for r in record["sdk_requests"]:
        print("SDK request", r["index"])
        for s in r["shape"]:
            print("  ", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
