"""Streaming Responses API example.

This demonstrates how to enable token streaming for the Responses API path,
log streaming deltas to ``./logs/stream/`` as JSON, and print the streamed text
incrementally to the terminal.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from openhands.sdk import (
    Conversation,
    ConversationCallbackType,
    LLMStreamChunk,
    get_logger,
)
from openhands.sdk.conversation.streaming_visualizer import create_streaming_visualizer
from openhands.sdk.conversation.visualizer import DefaultConversationVisualizer
from openhands.sdk.llm import LLM
from openhands.tools.preset.default import get_default_agent


PRINT_STREAM_TO_STDOUT = False


logger = get_logger(__name__)
LOG_DIR = Path("logs/stream")


def _serialize_event(event: LLMStreamChunk) -> dict[str, Any]:
    record = {
        "type": event.type,
        "part_kind": event.part_kind,
        "text": event.text_delta,
        "arguments": event.arguments_delta,
        "output_index": event.output_index,
        "content_index": event.content_index,
        "item_id": event.item_id,
        "response_id": event.response_id,
        "is_final": event.is_final,
    }
    return record


def main() -> None:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set LLM_API_KEY or OPENAI_API_KEY in your environment.")

    model = os.getenv("LLM_MODEL", "openhands/gpt-5-codex")
    base_url = os.getenv("LLM_BASE_URL")

    llm = LLM(
        model=model,
        api_key=SecretStr(api_key),
        base_url=base_url,
        usage_id="stream-demo",
    )

    agent = get_default_agent(llm=llm, cli_mode=True)

    timestamp = _dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"responses_stream_{timestamp}.jsonl"

    def on_token(event: LLMStreamChunk) -> None:
        record = _serialize_event(event)
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record) + "\n")

        delta = event.text_delta or event.arguments_delta
        if delta and PRINT_STREAM_TO_STDOUT:
            print(delta, end="", flush=True)
        if event.is_final and event.part_kind == "status" and PRINT_STREAM_TO_STDOUT:
            print("\n--- stream complete ---")

    callbacks: list[ConversationCallbackType] = []
    if not PRINT_STREAM_TO_STDOUT:
        streaming_visualizer = create_streaming_visualizer()
        callbacks.append(streaming_visualizer.on_event)

    conversation = Conversation(
        agent=agent,
        workspace=os.getcwd(),
        token_callbacks=[on_token],
        callbacks=callbacks or None,
        visualizer=None if callbacks else DefaultConversationVisualizer,
    )

    story_prompt = (
        "Tell me a long story about LLM streaming, make sure it has multiple "
        "paragraphs. Then write it on disk using a tool call."
    )
    conversation.send_message(story_prompt)
    conversation.run()

    cleanup_prompt = (
        "Thank you. Please delete the streaming story file now that I've read it, "
        "then confirm the deletion."
    )
    conversation.send_message(cleanup_prompt)
    conversation.run()

    logger.info("Stream log written to %s", log_path)


if __name__ == "__main__":
    main()
