"""Minimal native-API adapters for Databricks AI Gateway.

Everything in the OpenHands agent loop is OpenAI Chat Completions. This module
has one small adapter per non-default provider family — each ~30 LOC — that:

1. Converts an OpenAI-chat ``messages`` list + generation kwargs to the
   native request body for that family (``to_native``).
2. Converts the native response back to a minimal OpenAI ``ChatCompletion``
   dict that ``client._parse_response`` / ``litellm.ModelResponse`` accept
   unchanged (``from_native``).

Streaming is **not** adapted here — streaming stays on the universal OpenAI
Chat SSE path (``/invocations``) in ``client.py``. Native-API streaming
(Anthropic / Gemini / Responses) is a follow-up, documented in the skill.

Out-of-scope by design (documented, not silently stripped):

* Anthropic prompt caching, tool-use block preservation beyond plain text.
* Gemini multi-modal ``parts`` (inlineData, fileData).
* Responses ``custom`` / ``apply_patch`` / ``mcp`` tool types.

For these, use the native provider SDK directly against the Databricks
gateway ``base_url`` — see the companion skill's "Using provider SDKs
directly" section.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from openhands.sdk.llm.providers.databricks.models import ProviderFamily


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


def to_native(
    family: ProviderFamily,
    model: str,
    messages: list[dict],
    **kwargs: Any,
) -> dict:
    """Build the native request body for the given family.

    ``model`` is the bare endpoint name (no ``databricks/`` prefix).
    Unknown kwargs are passed through where the native API supports them
    and dropped where it doesn't.
    """
    if family is ProviderFamily.ANTHROPIC:
        return _to_anthropic(model, messages, **kwargs)
    if family is ProviderFamily.GEMINI:
        return _to_gemini(model, messages, **kwargs)
    if family is ProviderFamily.OPENAI_RESPONSES:
        return _to_responses(model, messages, **kwargs)
    return _to_openai_chat(model, messages, **kwargs)


def from_native(
    family: ProviderFamily,
    model: str,
    data: dict,
) -> dict:
    """Normalize a native response ``data`` to an OpenAI ChatCompletion dict."""
    if family is ProviderFamily.ANTHROPIC:
        return _from_anthropic(model, data)
    if family is ProviderFamily.GEMINI:
        return _from_gemini(model, data)
    if family is ProviderFamily.OPENAI_RESPONSES:
        return _from_responses(model, data)
    return _from_openai_chat(model, data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chat_completion(
    model: str,
    content: str,
    *,
    finish_reason: str = "stop",
    usage: dict | None = None,
    tool_calls: list[dict] | None = None,
    response_id: str | None = None,
) -> dict:
    """Build a minimal OpenAI-chat ``ChatCompletion`` dict."""
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": response_id or f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _flatten_content(content: Any) -> str:
    """Flatten OpenAI-style content (str | list of {type,text}) to plain str.

    Handles the reasoning-model shape where ``choices[0].message.content`` is
    a list of blocks (``{"type":"reasoning",...}``, ``{"type":"text","text":...}``).
    Skips ``reasoning`` blocks; concatenates ``text`` blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if not isinstance(blk, dict):
                continue
            t = blk.get("type")
            if t == "text" and isinstance(blk.get("text"), str):
                parts.append(blk["text"])
            # "reasoning" blocks are intentionally skipped.
        return "".join(parts)
    return ""


_GENERIC_KWARGS = {"temperature", "top_p", "stop"}


# ---------------------------------------------------------------------------
# OpenAI Chat (default)
# ---------------------------------------------------------------------------


def _to_openai_chat(model: str, messages: list[dict], **kwargs: Any) -> dict:
    # The AI Gateway ``/mlflow/v1/chat/completions`` endpoint reads the
    # target endpoint name from the body, not the URL — every other family
    # already does this, so the OpenAI Chat path now matches.
    body: dict[str, Any] = {"model": model, "messages": messages}
    if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
        body["max_tokens"] = kwargs["max_tokens"]
    if kwargs.get("tools"):
        body["tools"] = kwargs["tools"]
        if kwargs.get("tool_choice") is not None:
            body["tool_choice"] = kwargs["tool_choice"]
    for k in _GENERIC_KWARGS:
        if kwargs.get(k) is not None:
            body[k] = kwargs[k]
    if kwargs.get("stream"):
        body["stream"] = True
    return body


def _from_openai_chat(model: str, data: dict) -> dict:
    # Gateway already returns ChatCompletion shape; only normalize reasoning
    # models' list-of-blocks content back to a string so downstream consumers
    # don't need to special-case it.
    choices = data.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        if isinstance(msg.get("content"), list):
            msg["content"] = _flatten_content(msg["content"])
    return data


# ---------------------------------------------------------------------------
# Anthropic Messages
# ---------------------------------------------------------------------------


def _to_anthropic(model: str, messages: list[dict], **kwargs: Any) -> dict:
    """OpenAI messages → Anthropic Messages body.

    System messages become the top-level ``system`` string (Anthropic doesn't
    support ``role=system`` inside ``messages``). Tool calls beyond plain text
    are not converted here — use the Anthropic SDK against the gateway
    ``base_url`` if you need Anthropic-native tool_use blocks.
    """
    system_parts: list[str] = []
    conv: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = _flatten_content(m.get("content"))
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        if role in ("user", "assistant") and content:
            conv.append({"role": role, "content": content})
    body: dict[str, Any] = {
        "model": model,
        "messages": conv,
        # max_tokens is REQUIRED by Anthropic Messages; provide a safe default.
        "max_tokens": int(kwargs.get("max_tokens") or 1024),
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    if kwargs.get("temperature") is not None:
        body["temperature"] = kwargs["temperature"]
    if kwargs.get("top_p") is not None:
        body["top_p"] = kwargs["top_p"]
    if kwargs.get("stop"):
        stop = kwargs["stop"]
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    return body


_ANTHROPIC_STOP_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}


def _from_anthropic(model: str, data: dict) -> dict:
    text_parts: list[str] = []
    for blk in data.get("content") or []:
        if isinstance(blk, dict) and blk.get("type") == "text":
            text_parts.append(blk.get("text", ""))
    u = data.get("usage") or {}
    usage = {
        "prompt_tokens": u.get("input_tokens", 0),
        "completion_tokens": u.get("output_tokens", 0),
        "total_tokens": (u.get("input_tokens", 0) + u.get("output_tokens", 0)),
    }
    return _chat_completion(
        model,
        "".join(text_parts),
        finish_reason=_ANTHROPIC_STOP_MAP.get(data.get("stop_reason", ""), "stop"),
        usage=usage,
        response_id=data.get("id"),
    )


# ---------------------------------------------------------------------------
# Google Gemini generateContent
# ---------------------------------------------------------------------------


def _to_gemini(model: str, messages: list[dict], **kwargs: Any) -> dict:
    """OpenAI messages → Gemini ``generateContent`` body.

    ``role=system`` maps to ``systemInstruction``. OpenAI ``assistant`` becomes
    Gemini ``model``. ``maxOutputTokens`` defaults to 1024 — Gemini budgets
    *thinking + output* against this, so anything below ~256 can return empty.
    """
    system_parts: list[str] = []
    contents: list[dict] = []
    for m in messages:
        role = m.get("role")
        text = _flatten_content(m.get("content"))
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        g_role = "model" if role == "assistant" else "user"
        if text:
            contents.append({"role": g_role, "parts": [{"text": text}]})
    gen_config: dict[str, Any] = {
        "maxOutputTokens": int(kwargs.get("max_tokens") or 1024),
    }
    if kwargs.get("temperature") is not None:
        gen_config["temperature"] = kwargs["temperature"]
    if kwargs.get("top_p") is not None:
        gen_config["topP"] = kwargs["top_p"]
    if kwargs.get("stop"):
        stop = kwargs["stop"]
        gen_config["stopSequences"] = [stop] if isinstance(stop, str) else list(stop)
    body: dict[str, Any] = {"contents": contents, "generationConfig": gen_config}
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    return body


_GEMINI_FINISH_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "OTHER": "stop",
}


def _from_gemini(model: str, data: dict) -> dict:
    cands = data.get("candidates") or []
    text = ""
    finish = "stop"
    if cands:
        cand = cands[0]
        parts = ((cand.get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        finish = _GEMINI_FINISH_MAP.get(cand.get("finishReason", ""), "stop")
    um = data.get("usageMetadata") or {}
    usage = {
        "prompt_tokens": um.get("promptTokenCount", 0),
        "completion_tokens": um.get("candidatesTokenCount", 0),
        "total_tokens": um.get("totalTokenCount", 0),
    }
    return _chat_completion(
        model,
        text,
        finish_reason=finish,
        usage=usage,
        response_id=data.get("responseId"),
    )


# ---------------------------------------------------------------------------
# OpenAI Responses (GPT-5 series)
# ---------------------------------------------------------------------------

# Pay-per-token FM endpoints reject these — they're documented for the
# hosted OpenAI Responses API but not supported via the gateway.
_RESPONSES_DROP = {
    "background",
    "store",
    "previous_response_id",
    "service_tier",
    # ``max_completion_tokens`` is the Chat-Completions name; the upstream
    # LLM/litellm path emits it for OpenAI-flavoured calls. Responses uses
    # ``max_output_tokens`` (set explicitly above), so drop the chat-style
    # alias to avoid the API's ``unsupported_parameter`` 400.
    "max_completion_tokens",
    # GPT-5 reasoning models routed through ``/openai/v1/responses`` reject
    # both ``temperature`` and ``top_p`` ("Unsupported parameter…"). The
    # SDK still respects user intent by carrying the values into kwargs,
    # but the adapter drops them before they hit the wire so a single
    # ``temperature=0.0`` default doesn't break every GPT-5 call.
    "temperature",
    "top_p",
}


def _to_responses_input(messages: list[dict]) -> list[dict]:
    """Translate Chat-Completions messages into Responses API ``input`` items.

    The Responses API uses a flat item list instead of a ``messages`` array.
    This function handles all message types that appear in a multi-turn
    OpenHands conversation:

    Chat Completions role  → Responses input item(s)
    ─────────────────────────────────────────────────────────────────────────
    user / system          → {"role":"user/system","content":[{"type":"input_text","text":...}]}
    assistant (text only)  → {"role":"assistant","content":[{"type":"output_text","text":...}]}
    assistant (tool_calls) → one {"type":"function_call","call_id":...} item per tool call,
                             followed by an output_text item if there is also text content
    tool (result)          → {"type":"function_call_output","call_id":...,"output":...}
    ─────────────────────────────────────────────────────────────────────────

    Without the tool_calls and tool-result translations, multi-turn
    conversations where GPT-5 calls a tool fail on the second turn because
    the Responses API has no record of the function_call in the history.
    """
    role_to_part_type = {
        "user": "input_text",
        "system": "input_text",
        "developer": "input_text",
    }
    out: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        role = msg.get("role")
        content = msg.get("content")

        # ── tool result ─────────────────────────────────────────────────────
        if role == "tool":
            out.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id", ""),
                "output": str(_flatten_content(content)) if content else "",
            })
            continue

        # ── assistant with tool_calls (possibly also with text) ─────────────
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                out.append({
                    "type": "function_call",
                    "call_id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })
            # If the assistant message also contained text, emit it too.
            text = _flatten_content(content) if content else ""
            if text:
                out.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
            continue

        # ── text-only assistant messages ─────────────────────────────────────
        if role == "assistant":
            text = _flatten_content(content) if content else ""
            if text:
                out.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
            continue

        # ── user / system / developer ────────────────────────────────────────
        part_type = role_to_part_type.get(role)
        if part_type is None or content is None:
            # Unknown role — pass through unchanged so the gateway surfaces
            # a clear error rather than silently mangling the message.
            out.append(msg)
            continue

        if isinstance(content, str):
            translated_content: list[dict] = [
                {"type": part_type, "text": content}
            ]
        elif isinstance(content, list):
            translated_content = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    translated_content.append(
                        {"type": part_type, "text": c.get("text", "")}
                    )
                else:
                    translated_content.append(c)
        else:
            out.append(msg)
            continue

        new_msg = dict(msg)
        new_msg["content"] = translated_content
        out.append(new_msg)
    return out


def _chat_tools_to_responses(tools: list[dict]) -> list[dict]:
    """Convert Chat Completions tool format to Responses API format.

    Chat Completions:
        {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}

    Responses API:
        {"type": "function", "name": "...", "description": "...", "parameters": {...}}

    The Responses API requires ``name`` at the top level of each tool object.
    Passing the nested Chat Completions format causes a 400 "Missing required
    parameter: 'tools[0].name'" error from the gateway.
    """
    out: list[dict] = []
    for tool in tools:
        if not isinstance(tool, dict):
            out.append(tool)
            continue
        fn = tool.get("function")
        if tool.get("type") == "function" and isinstance(fn, dict):
            # Unwrap the function wrapper — Responses API is flat.
            converted: dict[str, Any] = {"type": "function"}
            converted.update(fn)
            out.append(converted)
        else:
            # Non-function tool or already in Responses format — pass through.
            out.append(tool)
    return out


def _to_responses(model: str, messages: list[dict], **kwargs: Any) -> dict:
    """OpenAI messages → OpenAI Responses body.

    Responses uses ``input`` (not ``messages``), ``max_output_tokens`` (not
    ``max_tokens``), and a different content-part vocabulary
    (``input_text`` / ``output_text`` instead of ``text``). Default budget
    is 1024 since GPT-5 spends tokens on reasoning before producing
    visible output.
    """
    body: dict[str, Any] = {
        "model": model,
        "input": _to_responses_input(messages),
        "max_output_tokens": int(kwargs.get("max_tokens") or 1024),
    }
    # NB: ``temperature`` / ``top_p`` are intentionally NOT forwarded —
    # see ``_RESPONSES_DROP`` above. GPT-5 reasoning models reject them.
    if kwargs.get("tools"):
        body["tools"] = _chat_tools_to_responses(kwargs["tools"])
        if kwargs.get("tool_choice") is not None:
            body["tool_choice"] = kwargs["tool_choice"]
    # Forward other allowed kwargs; silently drop known-unsupported ones.
    for k, v in kwargs.items():
        if k in _RESPONSES_DROP or v is None:
            continue
        if k in body or k in {"max_tokens", "tools", "tool_choice", "temperature",
                              "top_p", "stream", "stop"}:
            continue
        body[k] = v
    return body


def _from_responses(model: str, data: dict) -> dict:
    """Convert Responses API output to OpenAI Chat Completions shape.

    Responses returns an array of output items:
      * ``{"type":"message","content":[...]}``       → assistant text
      * ``{"type":"function_call","name":...}``       → tool_calls
      * ``{"type":"reasoning",...}``                  → skip (internal thinking)

    Tool calls are converted to the Chat Completions ``tool_calls`` array so
    OpenHands can dispatch them unchanged. Without this conversion GPT-5's tool
    invocations are silently dropped, causing the "response did not include a
    function call" loop.
    """
    text_parts: list[str] = []
    tool_calls: list[dict] = []

    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")

        if item_type == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                    text_parts.append(c.get("text", ""))

        elif item_type == "function_call":
            # Responses API function_call shape:
            #   {"type": "function_call", "id": "fc_...", "call_id": "call_...",
            #    "name": "terminal", "arguments": "{\"command\":\"ls\"}"}
            # → Chat Completions tool_calls shape:
            #   {"id": "call_...", "type": "function",
            #    "function": {"name": "terminal", "arguments": "..."}}
            call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", "{}"),
                },
            })
        # "reasoning" items are intentionally skipped.

    # Fallback: some responses expose aggregated text at `output_text`.
    if not text_parts and not tool_calls and isinstance(data.get("output_text"), str):
        text_parts.append(data["output_text"])

    u = data.get("usage") or {}
    usage = {
        "prompt_tokens": u.get("input_tokens", 0),
        "completion_tokens": u.get("output_tokens", 0),
        "total_tokens": u.get("total_tokens",
                              u.get("input_tokens", 0) + u.get("output_tokens", 0)),
    }
    finish = "tool_calls" if tool_calls else (
        "stop" if data.get("status") == "completed" else "stop"
    )

    msg: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls

    return {
        "id": data.get("id", f"resp-{uuid.uuid4().hex[:8]}"),
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": usage,
    }
