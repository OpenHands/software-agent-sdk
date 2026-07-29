"""Render SDK events as a resume-bootstrap transcript.

When an ACP-backed conversation must restart but the ACP server's own
session storage has been wiped (e.g. the sandbox was recycled), the
``session/load`` resume path is unavailable: the server has no record
of the session id we persisted. One workaround is to start a fresh
``new_session`` and replay the SDK's durable event history as the
opening user message — a "bootstrap-prompt resume".

This module provides the rendering primitive. The caller decides where
events come from (durable event store, in-memory state, …), how to
package the rendered string (e.g. as a ``SendMessageRequest``), and
what provider-specific post-processing to apply to the result (path
sanitization, output scrubbing, etc.).

The companion ``RESUME_CONTEXT_MARKER`` constant is exported so
producers and consumers can both detect an already-resumed message
without hard-coding the string.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openhands.sdk.event.acp_tool_call import ACPToolCallEvent, _block_field
from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import ActionEvent, MessageEvent
from openhands.sdk.llm import content_to_str


RESUME_CONTEXT_MARKER = "<<RESUMED CONVERSATION>>"
"""Header marker prefixing every bootstrap-resume transcript.

Both producers (the renderer) and consumers (callers that need to
detect an already-resumed message and avoid double-wrapping) reference
this constant so the contract is single-sourced.
"""

DEFAULT_HEADER_BODY = (
    "The conversation history below is from a prior session whose live "
    "context was lost. Treat it as background and continue from where "
    "the previous session left off."
)
DEFAULT_FOOTER = "--- End of prior session ---"

DEFAULT_MAX_CHARS = 60_000
DEFAULT_MAX_MESSAGE_CHARS = 8_000
DEFAULT_MAX_TOOL_CHARS = 2_000

_HEAD_ELLIPSIS = "...\n"
_TAIL_ELLIPSIS = "..."


def _truncate_keep_head(text: str, max_chars: int) -> str:
    """Truncate ``text`` to at most ``max_chars`` chars, keeping the start.

    Honors any non-negative ``max_chars`` strictly: ``max_chars=0`` yields
    ``""``, and values below 4 yield a plain slice (no room for an
    ellipsis marker). Used for per-message and per-tool-block caps where
    the opening content (question, command, file path) is what matters.
    """
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars < len(_TAIL_ELLIPSIS) + 1:
        return text[:max_chars]
    return text[: max_chars - len(_TAIL_ELLIPSIS)] + _TAIL_ELLIPSIS


def _truncate_keep_tail(text: str, max_chars: int) -> str:
    """Truncate ``text`` to at most ``max_chars`` chars, keeping the end.

    Honors any non-negative ``max_chars`` strictly. Used for the resume
    transcript body when the full history doesn't fit — the freshest
    events are the most useful context for an agent picking up where it
    left off, so we drop the oldest content first.
    """
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars < len(_HEAD_ELLIPSIS) + 1:
        return text[-max_chars:]
    return _HEAD_ELLIPSIS + text[-(max_chars - len(_HEAD_ELLIPSIS)) :]


def _render_message_event(event: MessageEvent, max_chars: int) -> str | None:
    role_label = "[USER]" if event.llm_message.role == "user" else "[ASSISTANT]"
    # ``to_llm_message`` folds ``extended_content`` (AgentContext / hook-provided
    # per-turn context) into ``content`` — exactly what the original LLM saw.
    # Reading ``llm_message.content`` alone would silently drop that context.
    message = event.to_llm_message()
    parts = [p for p in content_to_str(message.content) if p]
    text = "\n".join(parts).strip()
    if not text:
        return None
    return f"{role_label}: {_truncate_keep_head(text, max_chars)}"


def _render_action_event(event: ActionEvent, max_chars: int) -> str | None:
    # Built-in Actions (e.g. ``FinishAction``) expose a ``message`` field that
    # carries the agent's final summary for the turn. Other Actions don't, and
    # the LLMConvertible path renders them separately — skip silently.
    message = getattr(event.action, "message", None) if event.action else None
    if not isinstance(message, str) or not message.strip():
        return None
    return f"[AGENT]: {_truncate_keep_head(message.strip(), max_chars)}"


def _render_content_block(block: Any) -> str | None:
    """Render a single ACP content block as a compact summary.

    The ACP protocol defines three ``ToolCallContent`` variants —
    ``diff``, ``content`` (wrapping a generic ``ContentBlock``), and
    ``terminal`` — but ``_serialize_tool_content`` preserves direct
    ``ContentBlock`` dicts (``{type: "text", text: …}`` and friends) at
    the top level as well, so both shapes can appear in
    ``ACPToolCallEvent.content``.

    Returns a one-or-multi-line string, or ``None`` if the block carries
    no usable signal. All field reads accept both snake_case and
    camelCase naming conventions.
    """
    block_type = _block_field(block, "type")
    if block_type == "diff":
        path = _block_field(block, "path") or ""
        old_text = _block_field(block, "old_text", "oldText")
        kind = "patch" if old_text is not None else "write"
        header = f"[diff {kind}] {path}".rstrip()
        new_text = _block_field(block, "new_text", "newText")
        if isinstance(new_text, str) and new_text:
            return f"{header}\n{new_text}"
        return header
    if block_type == "content":
        inner = _block_field(block, "content")
        if inner is None:
            return None
        return _render_content_block(inner)
    if block_type == "terminal":
        tid = _block_field(block, "terminal_id", "terminalId")
        return f"[terminal {tid}]" if tid else "[terminal]"
    # Direct ContentBlock variants (also accepted at the top level — some
    # ACP servers emit them unwrapped, and ``_serialize_tool_content``
    # keeps that shape during persistence). Text content must be returned
    # verbatim so the resume transcript doesn't lose the actual payload.
    if block_type == "text":
        text = _block_field(block, "text")
        if isinstance(text, str) and text:
            return text
        return None
    if block_type == "image":
        return "[Image]"
    if block_type == "audio":
        return "[Audio]"
    # Any other named variant (resource, resource_link, …) renders as a
    # ``[<type>]`` placeholder.
    if block_type:
        return f"[{block_type}]"
    return None


def _render_content_list(content: list[Any] | None) -> list[str]:
    if not content:
        return []
    out: list[str] = []
    for block in content:
        rendered = _render_content_block(block)
        if rendered:
            out.append(rendered)
    return out


def _render_tool_event(event: ACPToolCallEvent, max_chars: int) -> str | None:
    # ACP streams ``pending → pending → completed`` for a single tool call;
    # placeholder events emitted before parameters arrive carry None for
    # input/output, False for is_error, and an empty/None content list.
    # Distinguish ``is None`` from falsey-but-valid payloads here: a tool
    # whose ``raw_output`` is ``0``, ``False``, or ``""`` is a *completed*
    # call with falsey result data, not a placeholder. Renderability:
    # something is present iff any of raw_input, raw_output, is_error, or
    # content carries actual information.
    if (
        event.raw_input is None
        and event.raw_output is None
        and not event.is_error
        and not event.content
    ):
        return None
    status = "failed" if event.is_error else (event.status or "completed")
    name = event.title or event.tool_kind or "tool"
    parts: list[str] = [f"[TOOL USE: {name}] ({status})"]
    if event.raw_input is not None:
        parts.append("  input:")
        for line in str(event.raw_input).splitlines() or [""]:
            parts.append(f"    {line}")
    content_lines = _render_content_list(event.content)
    if content_lines:
        parts.append("  content:")
        for entry in content_lines:
            for line in entry.splitlines() or [""]:
                parts.append(f"    {line}")
    if event.raw_output is not None:
        parts.append("  output:")
        for line in str(event.raw_output).splitlines() or [""]:
            parts.append(f"    {line}")
    return _truncate_keep_head("\n".join(parts), max_chars)


def _terminal_tool_indices(events: Sequence[Event]) -> set[int]:
    """Indices of the terminal ACPToolCallEvent for each *streaming sequence*.

    ACP emits an early ``started`` event and one terminal
    (``completed`` / ``failed``) event for a single tool call consecutively;
    only the terminal event carries the final I/O. We keep that terminal event
    and drop the earlier ``started`` one. (This also tolerates legacy logs
    persisted before the source collapse, where a ``pending → … → completed``
    run held several intermediates — keeping the last still picks the
    terminal frame.)

    Critically, dedup is scoped to **contiguous runs separated by
    MessageEvents**, not to the entire history. ACP providers (e.g. Codex)
    reset ``tool_call_id`` counters each session — after a sandbox recycle a
    new ``exec_1`` is entirely unrelated to the previous ``exec_1``. A
    ``MessageEvent`` is the natural session-boundary signal: if any message
    event falls between two occurrences of the same ``tool_call_id``, they
    belong to different sessions and both events render independently.
    """
    # Collect positions of MessageEvents (session boundary markers).
    msg_indices: set[int] = set()
    tool_positions: dict[str, list[int]] = {}
    for i, event in enumerate(events):
        if isinstance(event, MessageEvent):
            msg_indices.add(i)
        elif isinstance(event, ACPToolCallEvent) and event.tool_call_id:
            tool_positions.setdefault(event.tool_call_id, []).append(i)

    keep: set[int] = set()
    for positions in tool_positions.values():
        if len(positions) == 1:
            keep.add(positions[0])
            continue
        # Split into groups at MessageEvent boundaries.
        groups: list[list[int]] = [[positions[0]]]
        for idx in positions[1:]:
            prev = groups[-1][-1]
            if any(prev < m < idx for m in msg_indices):
                groups.append([idx])
            else:
                groups[-1].append(idx)
        for group in groups:
            keep.add(group[-1])

    return keep


def render_resume_transcript(
    events: Sequence[Event],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
    max_tool_chars: int = DEFAULT_MAX_TOOL_CHARS,
    marker: str = RESUME_CONTEXT_MARKER,
    header_body: str = DEFAULT_HEADER_BODY,
    footer: str = DEFAULT_FOOTER,
) -> str | None:
    """Render ``events`` as a single resume-bootstrap transcript string.

    Returns ``None`` when no event in ``events`` produces visible output
    (e.g. a fresh conversation, or only filtered placeholder tool events).
    Also returns ``None`` when ``max_chars < len(marker)``: a budget too
    small to hold the complete marker would force a partial marker, which
    would break ``out.startswith(marker)`` double-resume detection — so the
    caller should treat that case as a fresh conversation.

    ``MessageEvent``s become ``[USER]: …`` / ``[ASSISTANT]: …`` blocks
    (including ``extended_content`` from ``to_llm_message()``),
    ``ACPToolCallEvent``s become ``[TOOL USE: <name>] (<status>)`` blocks
    with raw input/output indented underneath, and ``ActionEvent``s whose
    ``action`` exposes a ``message`` (e.g. ``FinishAction``) become
    ``[AGENT]: …`` summary lines. Other event types are ignored.

    ``ACPToolCallEvent``s are deduplicated by ``tool_call_id``: only the
    final (terminal) event in each ACP streaming pending→completed
    sequence is rendered.

    Truncation is **tail-preserving** for the overall transcript: when the
    full history exceeds ``max_chars``, the marker and footer are kept
    and the oldest body content is dropped (with a ``"...\\n"`` prefix
    marking the cut). Per-message and per-tool caps are head-preserving —
    long individual blocks keep their opening text and append ``"..."``.

    All ``max_*`` parameters are honored strictly down to zero; the output
    string is guaranteed to satisfy ``len(result) <= max_chars``.

    The caller is responsible for:
      * passing events in chronological order (newest-first fetches must
        be reversed before being handed in);
      * any provider-specific scrubbing of tool ``raw_input`` /
        ``raw_output`` (path sanitization, filtering provider-internal
        metadata keys, stripping shell/test boilerplate, etc.);
      * packaging the rendered string into a ``SendMessageRequest`` or
        equivalent message envelope.
    """
    keep_tool_indices = _terminal_tool_indices(events)

    blocks: list[str] = []
    for i, event in enumerate(events):
        rendered: str | None
        if isinstance(event, MessageEvent):
            rendered = _render_message_event(event, max_message_chars)
        elif isinstance(event, ACPToolCallEvent):
            if event.tool_call_id and i not in keep_tool_indices:
                continue
            rendered = _render_tool_event(event, max_tool_chars)
        elif isinstance(event, ActionEvent):
            rendered = _render_action_event(event, max_message_chars)
        else:
            rendered = None
        if rendered:
            blocks.append(rendered)

    if not blocks:
        return None

    header_lines = [marker] + ([header_body] if header_body else [])
    header_text = "\n\n".join(header_lines)
    body_text = "\n\n".join(blocks)

    full = "\n\n".join([header_text, body_text, footer])
    if len(full) <= max_chars:
        return full

    # The transcript is too long. Preserve the marker, header body, and
    # footer literally; head-truncate the body so the freshest events
    # survive — that's the context an agent picking up the conversation
    # most needs.
    sep = "\n\n"
    overhead = len(header_text) + len(sep) + len(sep) + len(footer)
    body_budget = max_chars - overhead
    if body_budget >= len(_HEAD_ELLIPSIS) + 1:
        return (
            header_text
            + sep
            + _truncate_keep_tail(body_text, body_budget)
            + sep
            + footer
        )

    # ``max_chars`` is so tight that even header+footer alone don't leave
    # room for a meaningful body. Preserve the marker contract: every
    # non-None result must start with the complete ``marker`` string so that
    # callers can use ``out.startswith(marker)`` for double-resume detection.
    # A partial marker (e.g. ``<<RESUMED `` for max_chars=10) looks like
    # usable context but silently bypasses that guard. Return ``None`` when
    # the budget cannot fit the full marker — the caller should treat this
    # as a fresh conversation rather than as a malformed resume.
    if max_chars < len(marker):
        return None
    marker_prefix = marker + "\n"
    if len(marker_prefix) > max_chars:
        # Marker fits but no room for the newline separator — return just the
        # marker (without the newline) so the output is exactly max_chars.
        return marker
    # Fill the remaining budget with the freshest tail of the transcript.
    content_budget = max_chars - len(marker_prefix)
    return marker_prefix + _truncate_keep_tail(
        full[len(marker_prefix) :], content_budget
    )
