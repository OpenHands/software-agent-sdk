"""ACPToolCallEvent — surfaces ACP tool call trajectories as OpenHands events."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType


_MAX_DISPLAY_CHARS = 500

_MISSING: Any = object()


def _block_field(block: Any, *names: str) -> Any:
    """Read the first matching field from ``names`` on ``block``.

    ACP content blocks reach this code in three shapes:

      * Pydantic model (live notifications) — read via ``getattr``;
      * snake_case dict (after ``model_dump`` persistence) — Pydantic dumps
        using Python attribute names by default;
      * camelCase dict (ACP JSON wire format) — the ACP TypeScript spec
        defines diff blocks as ``{ type: "diff", oldText, newText, path }``,
        and JSON arriving from an external API or websocket frame keeps
        those keys verbatim.

    Multiple aliases (e.g. ``"old_text", "oldText"``) can be passed and the
    first one present wins. Returns ``None`` if no alias matches.
    """
    if isinstance(block, dict):
        for name in names:
            if name in block:
                return block[name]
        return None
    for name in names:
        value = getattr(block, name, _MISSING)
        if value is not _MISSING:
            return value
    return None


class ACPToolCallEvent(Event):
    """Event representing a tool call executed by an ACP server.

    Captures the tool name, inputs, outputs, and status from ACP
    ``ToolCallStart`` / ``ToolCallProgress`` notifications so they can
    be surfaced in the OpenHands event stream and visualizer.

    This is *not* an ``LLMConvertibleEvent`` — ACP tool calls do not
    participate in LLM message conversion.
    """

    source: SourceType = "agent"
    tool_call_id: str
    title: str
    status: str | None = None
    tool_kind: str | None = None
    raw_input: Any | None = None
    raw_output: Any | None = None
    content: list[Any] | None = None
    is_error: bool = False

    @property
    def is_patch_edit(self) -> bool:
        """True if this event represents a patch/diff edit (not a full-file write).

        ACP-spec edit tools emit a ``diff`` content block whose ``old_text``
        field (``oldText`` on the JSON wire) distinguishes the two cases:
          * patch edit (e.g. ``Edit``): ``old_text`` is set
          * full-file create (e.g. ``Write``): ``old_text`` is ``None``

        This check is provider-agnostic across Claude Code, Codex, and Gemini
        servers that follow the ACP spec.

        Robustness:
          * ``content`` is a list of mixed block variants (text, diff,
            terminal, …) in any order — this scans for ``diff`` blocks
            rather than assuming ``content[0]``.
          * A single tool call can carry **multiple** ``diff`` blocks (e.g.
            a multi-file patch that creates one file and edits another).
            The event is classified as a patch edit if *any* diff block
            has a non-null ``old_text``; it counts as a write only when
            *every* diff block is a full-file write.
          * Block shape may be a Pydantic model (live notifications), a
            snake_case dict (after ``model_dump``), or a camelCase dict
            (ACP JSON wire). ``_block_field`` reads from all three, with
            ``"oldText"`` accepted as an alias of ``"old_text"``.

        For providers that omit the structured content block but still
        expose the diff intent through raw input keys, the check falls back
        to ``raw_input``. The fallback requires a non-empty ``old_string`` —
        a ``new_string``-only payload (or empty ``old_string``) describes a
        create/write, not a patch. The fallback only applies when *no*
        ``diff`` block is present in ``content``; structured ACP data
        always overrides the heuristic.
        """
        diff_blocks = [
            b for b in self.content or () if _block_field(b, "type") == "diff"
        ]
        if diff_blocks:
            return any(
                _block_field(b, "old_text", "oldText") is not None for b in diff_blocks
            )
        raw = self.raw_input if isinstance(self.raw_input, dict) else {}
        old = raw.get("old_string")
        return isinstance(old, str) and len(old) > 0

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this tool call event."""
        content = Text()
        content.append(self.title, style="bold")

        # Kind / status metadata line
        meta_parts: list[str] = []
        if self.tool_kind:
            meta_parts.append(f"kind={self.tool_kind}")
        if self.status:
            meta_parts.append(f"status={self.status}")
        if meta_parts:
            content.append(f"\n{' | '.join(meta_parts)}", style="dim")

        # Input (skip None and empty containers like {})
        if self.raw_input:
            input_str = str(self.raw_input)
            if len(input_str) > _MAX_DISPLAY_CHARS:
                input_str = input_str[:_MAX_DISPLAY_CHARS] + "..."
            content.append("\nInput: ", style="bold")
            content.append(input_str)

        # Output (skip None and empty containers)
        if self.raw_output:
            output_str = str(self.raw_output)
            if len(output_str) > _MAX_DISPLAY_CHARS:
                output_str = output_str[:_MAX_DISPLAY_CHARS] + "..."
            content.append("\nOutput: ", style="bold")
            content.append(output_str)

        return content

    def __str__(self) -> str:
        parts = [f"{self.__class__.__name__} ({self.source}): {self.title}"]
        if self.status:
            parts.append(f"[{self.status}]")
        if self.tool_kind:
            parts.append(f"({self.tool_kind})")
        return " ".join(parts)
