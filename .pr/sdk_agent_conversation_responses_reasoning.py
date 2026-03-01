"""Agent-level check: Responses API + reasoning items in a real SDK Conversation.

This is adapted from `examples/01_standalone_sdk/23_responses_reasoning.py`, but
placed under `.pr/` for investigation-only.

It runs a short multi-turn agent conversation and prints a summary of whether the
assistant messages carry Responses reasoning items and whether they include
`encrypted_content`.

Usage:
    export OPENAI_API_KEY=sk-...
    uv run python .pr/sdk_agent_conversation_responses_reasoning.py

Env:
    LLM_MODEL (default: openai/o4-mini)
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import Conversation, Event, LLMConvertibleEvent
from openhands.sdk.llm import LLM
from openhands.tools.preset.default import get_default_agent


def _enc_len_from_llm_message(message) -> int | None:
    ri = getattr(message, "responses_reasoning_item", None)
    if ri is None:
        return None
    enc = getattr(ri, "encrypted_content", None)
    if isinstance(enc, str):
        return len(enc)
    return None


def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    assert api_key, "Set OPENAI_API_KEY"

    model = os.getenv("LLM_MODEL", "openai/o4-mini")

    llm = LLM(
        model=model,
        api_key=SecretStr(api_key),
        # keep defaults: store=False, enable_encrypted_reasoning=True
        reasoning_effort="high",
        log_completions=False,
        usage_id="agent",
    )

    # Keep all example workspace writes out of the git repo.
    ws = Path(".agent_tmp/sdk_agent_convo_ws").resolve()
    ws.mkdir(parents=True, exist_ok=True)

    agent = get_default_agent(llm=llm, cli_mode=True)

    llm_messages = []

    def cb(event: Event):
        if isinstance(event, LLMConvertibleEvent):
            llm_messages.append(event.to_llm_message())

    convo = Conversation(agent=agent, callbacks=[cb], workspace=str(ws))

    convo.send_message("Write a file FACTS.txt with one short line.")
    convo.run()

    convo.send_message("Now delete FACTS.txt.")
    convo.run()

    print("=== LLM messages (reasoning encrypted lengths) ===")
    for i, m in enumerate(llm_messages):
        role = getattr(m, "role", "?")
        enc_len = _enc_len_from_llm_message(m)
        has_ri = getattr(m, "responses_reasoning_item", None) is not None
        print(f"{i}: role={role} reasoning_item={has_ri} encrypted_len={enc_len}")


if __name__ == "__main__":
    main()
