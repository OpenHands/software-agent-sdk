"""Call a real `LLM` against the local endpoint while failing requests are
retried, and report the SDK's retry log lines, as a program's log handler
sees them. Every provider error body carries a fake secret marker.

Cases: a 503 then a reply (`num_retries=3`); a 400 (not retried); a 429 that
never clears (`num_retries=2`); a 2xx body that is not JSON, through the
OpenAI-compatible `hosted_vllm/` route, then a reply.
"""

import logging
import re

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import emit

from openhands.sdk import LLM
from openhands.sdk.llm import Message, TextContent


MARKER = "sk-leak-RETRYLOG-7731"
MESSAGES = [Message(role="user", content=[TextContent(text="Say hi.")])]
FAST = {"retry_min_wait": 0, "retry_max_wait": 0, "retry_multiplier": 0.1}
PREFIX = "LLM request failed:"


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


capture = Capture()
logging.getLogger().addHandler(capture)


def full_text(record: logging.LogRecord) -> str:
    text = record.getMessage()
    if record.exc_info:
        text += logging.Formatter().formatException(record.exc_info)
    return text


def run_case(script: list[dict], model: str = "openai/gpt-4o-mini", **kw) -> dict:
    start = len(capture.records)
    with FakeProvider(script) as server:
        llm = LLM(
            model=model,
            base_url=server.url,
            api_key=SecretStr("sk-test"),
            usage_id="direct",
            **FAST,
            **kw,
        )
        try:
            llm.completion(MESSAGES)
            error = None
        except Exception as exc:
            error = type(exc).__name__
        requests = len(server.requests)
    records = capture.records[start:]
    lines = [r for r in records if r.getMessage().startswith(PREFIX)]
    return {
        "error": error,
        "requests": requests,
        "lines": len(lines),
        "levels": [r.levelname for r in lines],
        "fields": [dict(re.findall(r"(\w+)=(\S+)", r.getMessage())) for r in lines],
        "marker_in_logs": any(MARKER in full_text(r) for r in records),
    }


secret_body = f"upstream said: Authorization: Bearer {MARKER}"
out: dict = {"ok": True}
out["transient"] = run_case(
    [{"status": 503, "message": secret_body}, {"text": "hi"}], num_retries=3
)
out["non_retryable"] = run_case(
    [{"status": 400, "message": f"Invalid value {MARKER}"}], num_retries=3
)
out["exhausted"] = run_case(
    [{"status": 429, "message": f"Rate limit reached {MARKER}"}] * 2, num_retries=2
)
out["malformed_2xx"] = run_case(
    [{"raw": f"<html>{secret_body}</html>"}, {"text": "hi"}],
    model="hosted_vllm/verify-model",
    num_retries=3,
)
out["marker_in_any_log"] = any(MARKER in full_text(r) for r in capture.records)
emit(out)
