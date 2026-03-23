"""SDK-level repro for Responses API stateless replay with reasoning items.

This script uses the OpenHands SDK's:
- Message formatting for Responses API (`format_messages_for_responses`)
- LiteLLM-backed Responses call path (`litellm.responses.main.responses`)

It tests whether requesting `include=["reasoning.encrypted_content"]` (via
`LLM.enable_encrypted_reasoning=True`) makes it safe to replay prior output items
(including `type:"reasoning"` with `rs_...` ids) when `store=False`.

Usage:
    export OPENAI_API_KEY=sk-...
    uv run python .pr/sdk_responses_stateless_replay_test.py

Optional:
    LLM_MODEL=openai/o4-mini uv run python .pr/sdk_responses_stateless_replay_test.py
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any

from pydantic import SecretStr

from litellm.responses.main import responses as litellm_responses

from openhands.sdk.llm import LLM
from openhands.sdk.llm.message import Message, TextContent
from openhands.sdk.llm.options.responses_options import select_responses_options


def _summarize_reasoning_item(msg: Message) -> dict[str, Any]:
    ri = msg.responses_reasoning_item
    if ri is None:
        return {"present": False}

    enc = ri.encrypted_content
    return {
        "present": True,
        "id": ri.id,
        "summary_len": len(ri.summary or []),
        "content_len": len(ri.content or []) if ri.content else 0,
        "encrypted_len": len(enc) if isinstance(enc, str) else None,
        "status": ri.status,
    }


def _first_turn(llm: LLM, *, prompt: str) -> Message:
    resp = llm.responses([Message(role="user", content=[TextContent(text=prompt)])])
    msg = resp.message
    assert isinstance(msg, Message)
    return msg


def _second_turn_manual_call(
    llm: LLM,
    *,
    prior_assistant: Message,
    user_text: str,
    bypass_sdk_strip: bool,
) -> tuple[bool, str]:
    msgs = [
        prior_assistant,
        Message(role="user", content=[TextContent(text=user_text)]),
    ]
    instructions, input_items = llm.format_messages_for_responses(msgs)

    if not bypass_sdk_strip:
        # Match SDK behavior on this branch: strip reasoning items when store=False.
        input_items = [
            it
            for it in input_items
            if not (isinstance(it, dict) and it.get("type") == "reasoning")
        ]

    call_kwargs = select_responses_options(llm, {}, include=None, store=False)

    api_key_value = llm.api_key.get_secret_value() if llm.api_key else None

    try:
        _ = litellm_responses(
            model=llm.model,
            input=input_items,
            instructions=instructions,
            api_key=api_key_value,
            api_base=llm.base_url,
            api_version=llm.api_version,
            timeout=llm.timeout,
            drop_params=llm.drop_params,
            **call_kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"

    return True, "ok"


def run_one(
    *,
    model: str,
    enable_encrypted_reasoning: bool,
    drop_params: bool,
    bypass_sdk_strip: bool,
) -> dict[str, Any]:
    llm = LLM(
        model=model,
        api_key=SecretStr(os.environ["OPENAI_API_KEY"]),
        drop_params=drop_params,
        enable_encrypted_reasoning=enable_encrypted_reasoning,
        log_completions=False,
    )

    out: dict[str, Any] = {
        "timestamp_utc": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "model": model,
        "enable_encrypted_reasoning": enable_encrypted_reasoning,
        "drop_params": drop_params,
        "bypass_sdk_strip": bypass_sdk_strip,
        "turn_1": {},
        "turn_2": {},
    }

    try:
        assistant = _first_turn(llm, prompt="What is 2+2? Be brief.")
    except Exception as exc:  # noqa: BLE001
        out["turn_1"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return out

    out["turn_1"] = {
        "ok": True,
        "assistant_text": "".join(
            [c.text for c in assistant.content if hasattr(c, "text")]
        ),
        "reasoning": _summarize_reasoning_item(assistant),
    }

    ok, detail = _second_turn_manual_call(
        llm,
        prior_assistant=assistant,
        user_text="Now what is 3+3? Be brief.",
        bypass_sdk_strip=bypass_sdk_strip,
    )
    out["turn_2"] = {"ok": ok, "detail": detail}
    return out


def main() -> None:
    model = os.getenv("LLM_MODEL", "openai/o4-mini")

    matrix: list[tuple[bool, bool, bool]] = [
        # (enable_encrypted_reasoning, drop_params, bypass_sdk_strip)
        (True, False, True),
        (False, False, True),
        (True, True, True),
        (False, True, True),
        # Also show current-branch SDK behavior (strip reasoning)
        (False, False, False),
    ]

    results = [
        run_one(
            model=model,
            enable_encrypted_reasoning=eer,
            drop_params=dp,
            bypass_sdk_strip=bypass,
        )
        for (eer, dp, bypass) in matrix
    ]

    print("# SDK stateless reasoning replay test (Responses API)\n")
    print(f"Run at (UTC): {_dt.datetime.now(tz=_dt.timezone.utc).isoformat()}")
    print(f"Model: {model}")
    print("")

    for r in results:
        cfg = (
            f"enable_encrypted_reasoning={r['enable_encrypted_reasoning']}, "
            f"drop_params={r['drop_params']}, "
            f"bypass_sdk_strip={r['bypass_sdk_strip']}"
        )
        print(f"## Config: {cfg}")
        t1 = r["turn_1"]
        if not t1.get("ok"):
            print(f"turn1: ERROR: {t1['error']}")
            print("")
            continue

        print("turn1: ok")
        print(f"  assistant_text: {t1.get('assistant_text')!r}")
        print(f"  reasoning: {t1.get('reasoning')}")

        t2 = r["turn_2"]
        print(f"turn2: {'OK' if t2['ok'] else 'FAIL'} :: {t2['detail']}")
        print("")

    print("---\nRaw JSON:")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    if "OPENAI_API_KEY" not in os.environ:
        raise RuntimeError("Set OPENAI_API_KEY in your environment")
    main()
