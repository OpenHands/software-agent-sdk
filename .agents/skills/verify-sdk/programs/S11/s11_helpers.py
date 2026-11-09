"""Helpers for the S11 programs (imported, not run).

`scripted(script, usage_id)` is make_llm(script) with a record of every call:
which LLM was asked, the call's prompt-cache key and session id, and the text
it was sent. Sub-agent factories use it to give each sub-agent its own
script.
"""

from typing import Any

from sdk_verify import script_messages

from openhands.sdk.testing import TestLLM


CALLS: list[dict[str, Any]] = []


class RecordingLLM(TestLLM):
    def completion(self, messages, *args, **kwargs):  # type: ignore[override]
        context = kwargs.get("call_context")
        CALLS.append(
            {
                "usage_id": self.usage_id,
                "prompt_cache_key": getattr(context, "prompt_cache_key", None),
                "session_id": getattr(context, "session_id", None),
                "text": "\n".join(
                    getattr(c, "text", "") for m in messages for c in (m.content or [])
                ),
            }
        )
        return super().completion(messages, *args, **kwargs)


def scripted(script: list[dict[str, Any]], usage_id: str) -> RecordingLLM:
    return RecordingLLM.from_messages(script_messages(script), usage_id=usage_id)


def calls_for(usage_id: str) -> list[dict[str, Any]]:
    return [c for c in CALLS if c["usage_id"] == usage_id]


def task_observations(events: Any) -> list[dict[str, Any]]:
    """The task tool's observations, in order."""
    out = []
    for e in events:
        obs = getattr(e, "observation", None)
        if obs is not None and type(obs).__name__ == "TaskObservation":
            out.append(
                {
                    "task_id": obs.task_id,
                    "subagent": obs.subagent,
                    "status": obs.status,
                    "is_error": obs.is_error,
                    "text": obs.text[:600],
                }
            )
    return out
