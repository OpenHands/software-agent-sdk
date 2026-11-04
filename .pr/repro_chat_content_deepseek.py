"""Compare SDK Chat serialization against DeepSeek; default is offline.

This tests Message.to_chat_dict() followed by direct HTTP, not LLM.completion().
Only the fixed system message uses string serialization. Target messages retain
the SDK's list serializer, without any subsequent content filtering.
"""

import argparse
import json
import logging
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


api_key = os.environ.get("LLM_API_KEY", "")
model = os.environ.get("LLM_MODEL", "deepseek-flash")
os.environ.clear()
os.environ.update(
    LITELLM_MODE="PRODUCTION",
    LITELLM_LOCAL_MODEL_COST_MAP="True",
    PYTHON_DOTENV_DISABLED="1",
    OPENHANDS_SUPPRESS_BANNER="1",
    LOG_LEVEL="CRITICAL",
    DO_NOT_TRACK="1",
)
logging.disable(logging.CRITICAL)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--live", action="store_true", help="Send up to five API requests")
args = parser.parse_args()


def block_offline_connections(event: str, _args: tuple[Any, ...]) -> None:
    if event == "socket.connect" and not args.live:
        raise RuntimeError("Dry-run forbids network connections")


sys.addaudithook(block_offline_connections)

from openhands.sdk.llm import Message, MessageToolCall, TextContent  # noqa: E402


ENDPOINT = "https://api.deepseek.com/chat/completions"


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def emit(record: dict[str, Any]) -> None:
    output = json.dumps(record, ensure_ascii=False)
    if api_key:
        output = output.replace(
            json.dumps(api_key, ensure_ascii=False)[1:-1], "<redacted>"
        )
    print(output, flush=True)


def git(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], text=True, stderr=subprocess.DEVNULL
    ).strip()


def cases() -> dict[str, list[Message]]:
    prompt = Message(role="user", content=[TextContent(text="Continue.")])
    tool_call = Message(
        role="assistant",
        content=[],
        tool_calls=[
            MessageToolCall(
                id="call-local", name="local_noop", arguments="{}", origin="completion"
            )
        ],
    )
    return {
        "control": [Message(role="user", content=[TextContent(text="Reply with OK.")])],
        "empty user": [Message(role="user", content=[])],
        "blank assistant": [
            prompt,
            Message(role="assistant", content=[TextContent(text=" \n\t")]),
            prompt,
        ],
        "mixed blank/valid user blocks": [
            Message(
                role="user",
                content=[TextContent(text="  "), TextContent(text="Continue.")],
            )
        ],
        "empty tool result": [
            prompt,
            tool_call,
            Message(
                role="tool", content=[], tool_call_id="call-local", name="local_noop"
            ),
            prompt,
        ],
    }


def serialize(message: Message, *, system: bool = False) -> dict[str, Any]:
    return message.to_chat_dict(
        cache_enabled=False,
        vision_enabled=False,
        function_calling_enabled=True,
        force_string_serializer=system,
        send_reasoning_content=False,
    )


def request(body: dict[str, Any]) -> dict[str, Any]:
    outgoing = Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with build_opener(NoRedirects()).open(outgoing, timeout=60) as response:
            status = response.status
            result = json.load(response)
    except HTTPError as error:
        try:
            details = json.load(error).get("error", {})
        except (ValueError, AttributeError):
            details = {}
        return {
            "outcome": "http_error",
            "http_status": error.code,
            "error": {
                key: details[key]
                for key in ("message", "code", "type", "param")
                if isinstance(details, dict) and isinstance(details.get(key), str)
            },
        }
    except URLError as error:
        return {"outcome": "transport_error", "error_type": type(error).__name__}
    choice = (result.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    return {
        "outcome": "completion_returned",
        "http_status": status,
        "response_model": result.get("model"),
        "finish_reason": choice.get("finish_reason"),
        "usage": result.get("usage"),
        "response_content": content[:200] if isinstance(content, str) else None,
    }


def main() -> int:
    if args.live and not api_key:
        parser.error("--live requires LLM_API_KEY")
    if not model or model.startswith("openai/"):
        parser.error(
            "LLM_MODEL must be a DeepSeek API model ID without a routing prefix"
        )
    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    source = Path(sys.modules[Message.__module__].__file__).resolve()
    if not source.is_relative_to(root / "openhands-sdk"):
        parser.error("Use the selected checkout's SDK environment or PYTHONPATH")
    emit(
        {
            "kind": "context",
            "mode": "live" if args.live else "dry-run",
            "path": "Message.to_chat_dict -> direct HTTP; not LLM.completion",
            "time_utc": datetime.now(UTC).isoformat(),
            "commit": git("rev-parse", "HEAD"),
            "tracked_changes": bool(git("diff", "HEAD", "--name-only")),
            "sdk_source": source.relative_to(root).as_posix(),
            "python": platform.python_version(),
            "platform": platform.system(),
            "sdk_version": version("openhands-sdk"),
            "litellm_version": version("litellm"),
            "endpoint": ENDPOINT,
            "model": model,
            "system_force_string_serializer": True,
            "target_force_string_serializer": False,
            "max_tokens": 128,
            "thinking": {"type": "disabled"},
            "retries": 0,
        }
    )
    system = Message(
        role="system",
        content=[TextContent(text="You are a helpful assistant. Reply briefly.")],
    )
    failures = 0
    for name, messages in cases().items():
        body = {
            "model": model,
            "messages": [serialize(system, system=True), *map(serialize, messages)],
            "max_tokens": 128,
            "thinking": {"type": "disabled"},
        }
        emit({"kind": "request_body", "case": name, "body": body})
        if not args.live:
            continue
        observation = request(body)
        emit({"kind": "observation", "case": name, **observation})
        if observation["outcome"] != "completion_returned":
            failures += 1
            if name == "control":
                emit({"kind": "summary", "interpretation": "Control failed; stopped."})
                return 2
    emit(
        {"kind": "summary", "errors": failures, "mode": "live" if args.live else "dry"}
    )
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        emit({"kind": "setup_error", "error_type": type(error).__name__})
        raise SystemExit(2) from None
