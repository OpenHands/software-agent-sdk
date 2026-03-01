"""Reproduce and validate Responses API behavior with reasoning items when store=False.

This is a standalone script (no SDK usage) intended for PR #2178 validation.

Usage:
    export OPENAI_API_KEY=sk-...
    uv run python .pr/repro_responses_reasoning_store_false.py

Notes:
- The failure mode being tested: passing prior `response.output` items (including
  `type: "reasoning"` items with `id: "rs_..."`) back into `input` for a later
  turn when `store=False`.
- Expected: turn 2 fails with `Item with id 'rs_...' not found...`.
- Workarounds tested here:
  - strip `type: "reasoning"` items from the next turn input
  - drop `id` fields from all items
  - keep only `{role, content}` from `type: "message"` items
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from typing import Any

from openai import OpenAI


def _item_to_dict(item: Any) -> Any:
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return item


def _summarize_output_items(items: list[Any]) -> str:
    parts: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            parts.append(type(it).__name__)
            continue
        it_type = it.get("type")
        it_id = it.get("id")
        if it_type and it_id:
            parts.append(f"{it_type}:{it_id}")
        elif it_type:
            parts.append(str(it_type))
        else:
            parts.append("<unknown>")
    return ", ".join(parts)


def _reasoning_item_ids(items: list[Any]) -> list[str]:
    ids: list[str] = []
    for it in items:
        if isinstance(it, dict) and it.get("type") == "reasoning" and it.get("id"):
            ids.append(str(it["id"]))
    return ids


def _reasoning_item_encrypted_lengths(items: list[Any]) -> list[int]:
    lens: list[int] = []
    for it in items:
        if not isinstance(it, dict) or it.get("type") != "reasoning":
            continue
        encrypted = it.get("encrypted_content")
        if isinstance(encrypted, str):
            lens.append(len(encrypted))
    return lens



def _strip_reasoning_items(items: list[Any]) -> list[Any]:
    return [
        it
        for it in items
        if not (isinstance(it, dict) and it.get("type") == "reasoning")
    ]


def _drop_ids(items: list[Any]) -> list[Any]:
    out: list[Any] = []
    for it in items:
        if isinstance(it, dict):
            it2 = dict(it)
            it2.pop("id", None)
            out.append(it2)
        else:
            out.append(it)
    return out


def _messages_only(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict) or it.get("type") != "message":
            continue
        role = it.get("role")
        content = it.get("content")
        if role is None or content is None:
            continue
        out.append({"role": role, "content": content})
    return out


def _attempt_turn_2(
    client: OpenAI,
    *,
    model: str,
    prior_items: list[Any],
    label: str,
    include: list[str] | None,
) -> tuple[bool, str]:
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "input": prior_items
            + [{"role": "user", "content": "Now what is 3+3? Be brief."}],
            "store": False,
        }
        if include is not None:
            kwargs["include"] = include

        resp2 = client.responses.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"

    text = getattr(resp2, "output_text", None)
    if isinstance(text, str) and text.strip():
        return True, f"ok: output_text={text!r}"

    out_items = [_item_to_dict(it) for it in getattr(resp2, "output", [])]
    return True, f"ok: output_items=[{_summarize_output_items(out_items)}]"


def run_for_model(model: str, *, include: list[str] | None) -> dict[str, Any]:
    client = OpenAI()

    result: dict[str, Any] = {
        "model": model,
        "include": include,
        "timestamp_utc": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "turn_1": {},
        "turn_2": {},
    }

    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "input": "What is 2+2? Be brief.",
            "store": False,
        }
        if include is not None:
            kwargs["include"] = include

        resp1 = client.responses.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        result["turn_1"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return result

    items1 = [_item_to_dict(it) for it in resp1.output]
    result["turn_1"] = {
        "ok": True,
        "output_text": getattr(resp1, "output_text", None),
        "output_items_summary": _summarize_output_items(items1),
        "reasoning_item_ids": _reasoning_item_ids(items1),
        "reasoning_item_encrypted_lengths": _reasoning_item_encrypted_lengths(items1),
    }

    attempts: list[dict[str, Any]] = []
    for label, prior in [
        ("naive_output_items", items1),
        ("strip_reasoning_items", _strip_reasoning_items(items1)),
        ("drop_ids", _drop_ids(items1)),
        ("messages_only", _messages_only(items1)),
    ]:
        ok, detail = _attempt_turn_2(
            client, model=model, prior_items=prior, label=label, include=include
        )
        attempts.append({"label": label, "ok": ok, "detail": detail})

    result["turn_2"]["attempts"] = attempts
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--include-encrypted",
        action="store_true",
        help=(
            "Set include=[\"reasoning.encrypted_content\"] on requests to get "
            "reasoning item encrypted_content for stateless multi-turn."
        ),
    )
    args = parser.parse_args()

    include = ["reasoning.encrypted_content"] if args.include_encrypted else None

    models = [
        "o4-mini",
        "gpt-5-nano",
        "gpt-5.2",
        "gpt-5.2-codex",
    ]

    results = [run_for_model(m, include=include) for m in models]

    print("# Responses API store=False reasoning-item behavior\n")
    print(f"Run at (UTC): {_dt.datetime.now(tz=_dt.timezone.utc).isoformat()}")
    print(f"include: {include}")
    print("Models:")
    for m in models:
        print(f"- {m}")
    print("")

    for r in results:
        print(f"## Model: {r['model']}")
        t1 = r["turn_1"]
        if not t1.get("ok"):
            print(f"turn1: ERROR: {t1.get('error')}")
            print("")
            continue

        print("turn1: ok")
        if t1.get("output_text"):
            print(f"  output_text: {t1['output_text']!r}")
        print(f"  output_items: {t1['output_items_summary']}")
        print(f"  reasoning_item_ids: {t1['reasoning_item_ids']}")
        print(
            "  reasoning_item_encrypted_lengths: "
            f"{t1['reasoning_item_encrypted_lengths']}"
        )

        print("turn2:")
        for a in r["turn_2"]["attempts"]:
            status = "OK" if a["ok"] else "FAIL"
            print(f"  - {a['label']}: {status} :: {a['detail']}")
        print("")

    print("\n---\nRaw JSON:")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
