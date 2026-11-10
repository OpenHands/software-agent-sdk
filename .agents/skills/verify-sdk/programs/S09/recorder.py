"""A scripted TestLLM that also records what each call was sent.

`make_llm(script=...)` from sdk_verify answers from a script; programs in this
directory also need to see the messages the SDK built (system prompt blocks,
user-message suffixes, summary prompts), so they subclass TestLLM once here.
"""

from __future__ import annotations

from typing import Any

from sdk_verify import script_messages

from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM


CALLS: list[dict[str, Any]] = []


class RecordingLLM(TestLLM):
    def completion(self, messages, tools=None, *args: Any, **kwargs: Any):  # type: ignore[override]
        CALLS.append(
            {
                "usage_id": self.usage_id,
                "stream": self.stream,
                "messages": list(messages),
                "tools": [t.name for t in tools or []],
                "schemas": {
                    t.name: t.to_openai_tool(
                        add_security_risk_prediction=kwargs.get(
                            "add_security_risk_prediction", False
                        )
                    )
                    for t in tools or []
                },
                "kwargs": sorted(kwargs),
            }
        )
        return super().completion(messages, tools, *args, **kwargs)


def recording_llm(
    script: list[dict[str, Any]], *, usage_id: str = "agent", **kwargs: Any
) -> RecordingLLM:
    return RecordingLLM.from_messages(
        script_messages(script), usage_id=usage_id, **kwargs
    )


def calls(usage_id: str) -> list[dict[str, Any]]:
    return [c for c in CALLS if c["usage_id"] == usage_id]


def text(message: Message) -> str:
    return "\n".join(
        c.text for c in message.content or [] if isinstance(c, TextContent)
    )


def blocks(message: Message) -> list[str]:
    return [c.text for c in message.content or [] if isinstance(c, TextContent)]
