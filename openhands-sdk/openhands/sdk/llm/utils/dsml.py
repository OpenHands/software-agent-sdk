"""DeepSeek Markup Language (DSML) parsing and normalization for DeepSeek V4 models.

DeepSeek V4 models may format tool calls using an XML-based markup syntax
(`<｜DSML｜tool_calls>`, `<｜｜DSML｜｜r=...>`, etc.) instead of the standard OpenAI
`tool_calls` structure, especially when tool calling is not natively bridged
or when models fall back to internal tool calling markup.

This module normalizes DSML markup from `content` into standard OpenAI-compatible
`ChatCompletionMessageToolCall` structures, strips the markup from `content`, and
provides a streaming filter to prevent DSML tokens from leaking to user-visible
`on_token` callbacks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from litellm import (
    ChatCompletionMessageToolCall,
    ModelResponse,
    ModelResponseStream,
)
from litellm.types.utils import Function


logger = logging.getLogger(__name__)

# Markers for DSML blocks. Covers both fullwidth (｜, U+FF5C) and standard ASCII (|)
_DSML_MARKER_PATTERN = r"(?:\|\|DSML\|\||｜｜DSML｜｜|\|DSML\||｜DSML｜)"
_DSML_DETECTION_RE = re.compile(_DSML_MARKER_PATTERN, re.IGNORECASE)

_DEEPSEEK_V4_PATTERNS = ("deepseek-v4", "deepseek_v4")


def is_deepseek_v4_model(model: str | None) -> bool:
    """Check if the model string corresponds to a DeepSeek V4 model."""
    if not model:
        return False
    raw = model.strip().lower()
    return any(p in raw for p in _DEEPSEEK_V4_PATTERNS)


def has_dsml_markers(text: str | None) -> bool:
    """Check if the text contains any DSML markers."""
    if not text:
        return False
    return bool(_DSML_DETECTION_RE.search(text))


def _generate_call_id(tool_name: str, arguments: dict[str, Any], index: int) -> str:
    """Generate a stable, unique OpenAI-style tool call ID."""
    args_json = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(f"{tool_name}:{args_json}:{index}".encode()).hexdigest()[:20]
    return f"call_dsml_{h}"


def parse_dsml_tool_calls(
    content: str,
) -> tuple[list[ChatCompletionMessageToolCall], str]:
    """Parse DSML tool calls from assistant message content.

    Returns:
        (tool_calls, cleaned_content)
        If no tool calls are parsed or DSML is unrecoverable, returns ([], content).
    """
    if not content or not has_dsml_markers(content):
        return [], content

    raw_calls: list[tuple[str, dict[str, Any]]] = []

    # 1. First check standard DSML block:
    # <[|]DSML[|]tool_calls> ... </[|]DSML[|]tool_calls>
    std_block_re = re.compile(
        rf"<(?:\s*{_DSML_MARKER_PATTERN}\s*)tool_calls\s*>\s*(.*?)(?:</(?:\s*{_DSML_MARKER_PATTERN}\s*)tool_calls\s*>|$)",
        re.DOTALL | re.IGNORECASE,
    )

    std_match = std_block_re.search(content)
    if std_match:
        block_content = std_match.group(1)
        invoke_tag_re = re.compile(
            rf"<(?:\s*{_DSML_MARKER_PATTERN}\s*)?invoke\s+(?:name\s*=\s*|\s*=\s*)?[\"']?([A-Za-z0-9_.-]+)[\"']?[^>]*>",
            re.IGNORECASE,
        )

        invoke_splits = list(invoke_tag_re.finditer(block_content))
        for i, inv_m in enumerate(invoke_splits):
            tool_name = inv_m.group(1)
            start_pos = inv_m.end()
            end_pos = (
                invoke_splits[i + 1].start()
                if i + 1 < len(invoke_splits)
                else len(block_content)
            )
            inv_body = block_content[start_pos:end_pos]
            inv_body = re.sub(
                rf"</(?:\s*{_DSML_MARKER_PATTERN}\s*)?invoke\s*>",
                "",
                inv_body,
                flags=re.IGNORECASE,
            )

            param_tag_re = re.compile(
                rf"<(?:\s*{_DSML_MARKER_PATTERN}\s*)?parameter\s+(?:name\s*=\s*|\s*=\s*)?[\"']?([A-Za-z0-9_.-]+)[\"']?(?:\s+string\s*=\s*[\"']?(true|false)[\"']?)?[^>]*>",
                re.IGNORECASE,
            )
            param_splits = list(param_tag_re.finditer(inv_body))
            params: dict[str, Any] = {}
            for j, p_m in enumerate(param_splits):
                pname = p_m.group(1)
                is_str_val = p_m.group(2)
                p_start = p_m.end()
                p_end = (
                    param_splits[j + 1].start()
                    if j + 1 < len(param_splits)
                    else len(inv_body)
                )
                pval_raw = inv_body[p_start:p_end]
                pval_raw = re.sub(
                    rf"</(?:\s*{_DSML_MARKER_PATTERN}\s*)?parameter\s*>",
                    "",
                    pval_raw,
                    flags=re.IGNORECASE,
                )
                if is_str_val and is_str_val.lower() == "false":
                    stripped = pval_raw.strip()
                    try:
                        parsed_val = json.loads(stripped)
                    except Exception:
                        parsed_val = stripped
                else:
                    parsed_val = pval_raw.strip("\r\n")

                params[pname] = parsed_val

            raw_calls.append((tool_name, params))

        cleaned_content = std_block_re.sub("", content).strip()
        cleaned_content = re.sub(
            rf"</?(?:\s*{_DSML_MARKER_PATTERN}\s*)[^>]*>",
            "",
            cleaned_content,
        ).strip()

        if not raw_calls:
            logger.warning(
                "Malformed DeepSeek V4 DSML markup detected; "
                "cannot safely recover tool calls."
            )
            return [], content

        return _build_tool_calls(raw_calls), cleaned_content

    # 2. Check compact / evaluation log variants: <||DSML||r=tool_name> ...
    compact_tag_re = re.compile(
        rf"<(?:\s*{_DSML_MARKER_PATTERN}\s*)(?:r|func|function|invoke)\s*=\s*[\"']?([A-Za-z0-9_.-]+)[\"']?[^>]*>",
        re.IGNORECASE,
    )
    compact_splits = list(compact_tag_re.finditer(content))
    if compact_splits:
        for i, c_m in enumerate(compact_splits):
            tool_name = c_m.group(1)
            start_pos = c_m.end()
            end_pos = (
                compact_splits[i + 1].start()
                if i + 1 < len(compact_splits)
                else len(content)
            )
            body = content[start_pos:end_pos]

            param_re = re.compile(
                rf"<parameter\s*=\s*[\"']?([A-Za-z0-9_.-]+)[\"']?[^>]*>(.*?)(?:</parameter>|(?=<parameter|</{_DSML_MARKER_PATTERN}|$))",
                re.DOTALL | re.IGNORECASE,
            )
            param_matches = list(param_re.finditer(body))
            if param_matches:
                params: dict[str, Any] = {
                    pm.group(1): pm.group(2).strip("\r\n") for pm in param_matches
                }
                raw_calls.append((tool_name, params))
            else:
                m_re = re.compile(
                    rf"<(?:\s*{_DSML_MARKER_PATTERN}\s*)m\s*>(.*?)(?:</(?:\s*{_DSML_MARKER_PATTERN}\s*)m\s*>|(?=<(?:\s*{_DSML_MARKER_PATTERN}\s*)m\s*>|</{_DSML_MARKER_PATTERN}|$))",
                    re.DOTALL | re.IGNORECASE,
                )
                m_matches = list(m_re.finditer(body))
                if m_matches:
                    for mm in m_matches:
                        val = mm.group(1).strip("\r\n")
                        raw_calls.append((tool_name, {"command": val}))
                else:
                    unclosed_m = re.search(
                        rf"(.*?)(?:</(?:\s*{_DSML_MARKER_PATTERN}\s*)[^>]*>|$)",
                        body,
                        re.DOTALL,
                    )
                    if unclosed_m and unclosed_m.group(1).strip():
                        val = unclosed_m.group(1).strip("\r\n")
                        raw_calls.append((tool_name, {"command": val}))

        cleaned_content = compact_tag_re.sub("", content)
        cleaned_content = re.sub(
            r"<parameter\s*=\s*[^>]*>.*?(?:</parameter>|$)",
            "",
            cleaned_content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned_content = re.sub(
            rf"<(?:\s*{_DSML_MARKER_PATTERN}\s*)m\s*>.*?(?:</(?:\s*{_DSML_MARKER_PATTERN}\s*)m\s*>|$)",
            "",
            cleaned_content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned_content = re.sub(
            rf"</?(?:\s*{_DSML_MARKER_PATTERN}\s*)[^>]*>",
            "",
            cleaned_content,
        ).strip()

        if not raw_calls:
            logger.warning(
                "Malformed DeepSeek V4 DSML markup detected; "
                "cannot safely recover tool calls."
            )
            return [], content

        return _build_tool_calls(raw_calls), cleaned_content

    logger.warning(
        "Malformed DeepSeek V4 DSML markup detected; cannot safely recover tool calls."
    )
    return [], content


def _build_tool_calls(
    raw_calls: list[tuple[str, dict[str, Any]]],
) -> list[ChatCompletionMessageToolCall]:
    """Build list of ChatCompletionMessageToolCall with stable, unique IDs."""
    tool_calls: list[ChatCompletionMessageToolCall] = []
    for index, (tool_name, arguments) in enumerate(raw_calls):
        call_id = _generate_call_id(tool_name, arguments, index)
        tool_calls.append(
            ChatCompletionMessageToolCall(
                id=call_id,
                type="function",
                function=Function(
                    name=tool_name,
                    arguments=json.dumps(arguments, ensure_ascii=False),
                ),
            )
        )
    return tool_calls


def normalize_deepseek_v4_response(
    resp: ModelResponse,
    model: str | None = None,
) -> ModelResponse:
    """Normalize DeepSeek V4 ModelResponse if DSML tool calls are in content.

    Only applies when:
    - The model belongs to DeepSeek V4
    - Existing structured tool_calls is empty/None
    - Content contains DSML markers
    """
    if not is_deepseek_v4_model(model):
        return resp

    if not resp.choices:
        return resp

    choice = resp.choices[0]
    message = getattr(choice, "message", None)
    if not message:
        return resp

    # Do not reprocess already structured tool calls
    existing_tool_calls = getattr(message, "tool_calls", None)
    if existing_tool_calls:
        return resp

    content = getattr(message, "content", None)
    if not isinstance(content, str) or not has_dsml_markers(content):
        return resp

    parsed_calls, cleaned_content = parse_dsml_tool_calls(content)
    if parsed_calls:
        message.tool_calls = parsed_calls
        message.content = cleaned_content
        if getattr(choice, "finish_reason", None) in (None, "stop"):
            choice.finish_reason = "tool_calls"

    return resp


class DSMLStreamFilter:
    """Streaming token filter that suppresses DSML markup from token callbacks.

    Plain conversational text (e.g. reasoning/thoughts before the DSML block)
    is streamed immediately. When potential DSML markup prefixes are encountered,
    tokens are held in an internal buffer. If a DSML marker is confirmed, the DSML
    tokens are suppressed. If the candidate turns out not to be DSML (e.g. `x < y`),
    the buffered characters are flushed to the callback.
    """

    def __init__(self, callback: Callable[[Any], Any]) -> None:
        self.callback = callback
        self.buffer = ""
        self.in_dsml = False
        self._marker_prefixes = [
            "<",
            "<|",
            "<｜",
            "<||",
            "<｜｜",
            "<|D",
            "<｜D",
            "<|DS",
            "<｜DS",
            "<|DSM",
            "<｜DSM",
            "<|DSML",
            "<｜DSML",
            "<||D",
            "<｜｜D",
            "<||DS",
            "<｜｜DS",
            "<||DSM",
            "<｜｜DSM",
            "<||DSML",
            "<｜｜DSML",
            "<|DSML|",
            "<｜DSML｜",
            "<||DSML||",
            "<｜｜DSML｜｜",
            "</",
            "</|",
            "</｜",
            "</||",
            "</｜｜",
            "</|D",
            "</｜D",
            "</|DS",
            "</｜DS",
            "</|DSM",
            "</｜DSM",
            "</|DSML",
            "</｜DSML",
            "</||D",
            "</｜｜D",
            "</||DS",
            "</｜｜DS",
            "</||DSM",
            "</｜｜DSM",
            "</||DSML",
            "</｜｜DSML",
            "</|DSML|",
            "</｜DSML｜",
            "</||DSML||",
            "</｜｜DSML｜｜",
        ]

    def _is_prefix_of_marker(self, text: str) -> bool:
        return any(p.startswith(text) for p in self._marker_prefixes)

    def _contains_marker(self, text: str) -> bool:
        return any(
            m in text
            for m in (
                "<|DSML|",
                "<｜DSML｜",
                "<||DSML||",
                "<｜｜DSML｜｜",
                "</|DSML|",
                "</｜DSML｜",
                "</||DSML||",
                "</｜｜DSML｜｜",
            )
        )

    def filter_chunk(self, chunk: ModelResponseStream) -> ModelResponseStream | None:
        """Filter a ModelResponseStream chunk.

        Returns a chunk with filtered delta content, or None if the chunk's content
        is entirely suppressed.
        """
        if not chunk.choices:
            return chunk

        delta = getattr(chunk.choices[0], "delta", None)
        if delta is None:
            return chunk

        content_str = getattr(delta, "content", None)
        if content_str is None or not isinstance(content_str, str):
            return chunk

        emits = []
        for char in content_str:
            if self.in_dsml:
                self.buffer += char
                if re.search(
                    rf"</(?:\s*{_DSML_MARKER_PATTERN}\s*)(?:tool_calls|r)[^>]*>",
                    self.buffer,
                ):
                    self.in_dsml = False
                    self.buffer = ""
            elif self.buffer:
                candidate = self.buffer + char
                if self._is_prefix_of_marker(candidate):
                    self.buffer = candidate
                    if self._contains_marker(candidate):
                        if re.search(
                            rf"</(?:\s*{_DSML_MARKER_PATTERN}\s*)(?:tool_calls|r|m)?[^>]*>",
                            self.buffer,
                        ):
                            self.in_dsml = False
                            self.buffer = ""
                        else:
                            self.in_dsml = True
                            self.buffer = ""
                else:
                    emits.append(self.buffer)
                    self.buffer = ""
                    if char == "<":
                        self.buffer = "<"
                    else:
                        emits.append(char)
            else:
                if char == "<":
                    self.buffer = "<"
                else:
                    emits.append(char)

        emitted_text = "".join(emits)
        if not emitted_text:
            return None

        new_chunk = copy.copy(chunk)
        new_choice = copy.copy(chunk.choices[0])
        new_delta = copy.copy(delta)
        new_delta.content = emitted_text
        new_choice.delta = new_delta
        new_chunk.choices = [new_choice]
        return new_chunk

    def flush_remaining(self) -> str | None:
        """Flush any pending non-DSML buffered text."""
        if not self.in_dsml and self.buffer:
            res = self.buffer
            self.buffer = ""
            return res
        return None
