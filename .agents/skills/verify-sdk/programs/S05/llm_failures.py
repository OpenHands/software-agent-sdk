"""Run conversations whose real `LLM` keeps failing against the local
endpoint, and report the `ConversationErrorEvent` each run ends with and its
classification. One scenario per mode.

Usage: llm_failures.py MODE
  exhausted  every attempt reaches the hard timeout (`timeout=1`,
             `num_retries=5`); every attempt is content-free; plus the
             classification of a single quick timeout's text
  quick      every attempt hits a stream idle timeout far below `timeout`
             (`stream_idle_timeout=0.3`, `timeout=30`, `num_retries=3`)
"""

import asyncio
import sys

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import emit, run_paths

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.error_classification import classify_error


mode = sys.argv[1]
out: dict = {"ok": True, "mode": mode}
FAST = {"retry_min_wait": 0, "retry_max_wait": 0, "retry_multiplier": 0.1}


def failing_run(script: list[dict], streamed: bool = False, **llm_kwargs) -> dict:
    with FakeProvider(script) as server:
        llm = LLM(
            model="openai/gpt-4o-mini",
            base_url=server.url,
            api_key=SecretStr("sk-test"),
            usage_id="agent",
            stream=streamed,
            **FAST,
            **llm_kwargs,
        )
        conversation = Conversation(
            agent=Agent(llm=llm, tools=[]),
            workspace=str(run_paths()["workspace"]),
            visualizer=None,
            token_callbacks=[lambda _chunk: None] if streamed else None,
        )
        conversation.send_message("Answer.")
        try:
            if streamed:
                asyncio.run(conversation.arun())
            else:
                conversation.run()
            raised = None
        except Exception as exc:
            raised = type(exc).__name__
        errors = [
            e
            for e in conversation.state.events
            if isinstance(e, ConversationErrorEvent)
        ]
        result = {
            "status": conversation.state.execution_status.value,
            "raised": raised,
            "requests": len(server.requests),
            "errors": len(errors),
            "code": errors[0].code if errors else None,
            "detail": errors[0].detail if errors else None,
            # The classification as the serialized event carries it.
            "classification": (
                errors[0].model_dump(mode="json").get("classification")
                if errors
                else None
            ),
        }
        conversation.close()
        return result


if mode == "exhausted":
    never = {"delay": 30, "text": "never sent"}
    out["hard_timeout"] = failing_run([never] * 6, timeout=1, num_retries=5)
    out["content_free"] = failing_run([{"text": ""}] * 3, num_retries=2)
    single = classify_error("LLMTimeoutError", "litellm.Timeout: Connection timed out.")
    out["single_quick_timeout"] = single.model_dump(mode="json")

elif mode == "quick":
    stall = {"stall": 5, "text": "stalled"}
    out["quick_timeout"] = failing_run(
        [stall] * 4,
        streamed=True,
        timeout=30,
        stream_idle_timeout=0.3,
        num_retries=3,
    )

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
