import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from flight_recorder.models.envelopes import Record
from pydantic import ValidationError

from openhands.sdk.llm.message import ImageContent, Message, TextContent


class SummaryTone(StrEnum):
    OPENHANDS = "openhands"
    USER = "user"
    AGENT = "agent"


@dataclass(frozen=True)
class SummaryHighlight:
    start: int
    end: int
    tone: SummaryTone


@dataclass(frozen=True)
class StyledSummary:
    text: str
    highlights: tuple[SummaryHighlight, ...] = ()


def format_record_summary(record: Record) -> str:
    return format_styled_record_summary(record).text


def format_styled_record_summary(
    record: Record, related_user_message: Record | None = None
) -> StyledSummary:
    event_type = record.payload.get("event_type")
    if event_type == "MessageEvent":
        message_summary = _format_message_summary(record)
        if message_summary is not None:
            return message_summary
    elif event_type == "SystemPromptEvent":
        system_prompt_summary = _format_system_prompt_summary(
            record, related_user_message
        )
        if system_prompt_summary is not None:
            return system_prompt_summary
    return StyledSummary(
        text=(
            f"{record.kind}\nSequence: {record.sequence}\n"
            f"Agent: {record.agent_span_id or 'None'}\n"
            f"Payload: {json.dumps(record.payload, indent=2, sort_keys=True)}"
        )
    )


def _format_message_summary(record: Record) -> StyledSummary | None:
    raw_message = record.payload.get("llm_message")
    if not isinstance(raw_message, dict):
        return None
    try:
        message = Message.model_validate(raw_message)
    except ValidationError:
        return None

    lines = [
        "Message",
        f"Sequence: {record.sequence}",
        f"Agent: {record.agent_span_id or 'None'}",
        f"Source: {_metadata_value(record.payload, 'source')}",
        f"Role: {message.role}",
    ]
    _append_metadata(lines, record.payload, "sender", "Sender")
    _append_metadata(lines, record.payload, "timestamp", "Timestamp")
    _append_metadata(lines, record.payload, "id", "Message ID")
    _append_metadata(lines, record.payload, "llm_response_id", "LLM response ID")

    source = _metadata_value(record.payload, "source")
    if source == "user":
        content_label = "USER PROMPT"
        content_tone = SummaryTone.USER
    else:
        content_label = "AGENT MESSAGE"
        content_tone = SummaryTone.AGENT
    content_section = f"{content_label}\n{_format_content(message.content)}"
    lines.extend(("", content_section))
    highlighted_sections = [(content_section, content_tone)]

    extended_content = _parse_extended_content(record.payload)
    if extended_content:
        extension_section = "OPENHANDS PROMPT EXTENSION\n" + _format_content(
            extended_content
        )
        lines.extend(("", extension_section))
        highlighted_sections.append((extension_section, SummaryTone.OPENHANDS))

    activated_skills = record.payload.get("activated_skills")
    if isinstance(activated_skills, list):
        skill_names = [skill for skill in activated_skills if isinstance(skill, str)]
        if skill_names:
            skills_section = f"Activated skills: {', '.join(skill_names)}"
            lines.extend(("", skills_section))
            highlighted_sections.append((skills_section, SummaryTone.OPENHANDS))

    if message.tool_calls:
        tool_names = ", ".join(tool_call.name for tool_call in message.tool_calls)
        lines.extend(("", f"Tool calls: {tool_names}"))

    return _style_sections("\n".join(lines), highlighted_sections)


def _format_system_prompt_summary(
    record: Record, related_user_message: Record | None
) -> StyledSummary | None:
    system_prompt = _parse_text_content(record.payload.get("system_prompt"))
    if system_prompt is None:
        return None

    lines = [
        "System prompt",
        f"Sequence: {record.sequence}",
        f"Agent: {record.agent_span_id or 'None'}",
        f"Source: {_metadata_value(record.payload, 'source')}",
    ]
    _append_metadata(lines, record.payload, "timestamp", "Timestamp")
    _append_metadata(lines, record.payload, "id", "Event ID")
    highlighted_sections = []
    user_request = _format_related_user_request(related_user_message)
    if user_request is not None:
        lines.extend(("", user_request))
        highlighted_sections.append((user_request, SummaryTone.USER))
    system_section = (
        f"OPENHANDS TEMPLATE\nSYSTEM INSTRUCTIONS\n{_format_text(system_prompt.text)}"
    )
    lines.extend(("", system_section))
    highlighted_sections.append((system_section, SummaryTone.OPENHANDS))

    dynamic_context = _parse_text_content(record.payload.get("dynamic_context"))
    if dynamic_context is not None:
        context_section = "OPENHANDS DYNAMIC CONTEXT\n" + _format_text(
            dynamic_context.text
        )
        lines.extend(("", context_section))
        highlighted_sections.append((context_section, SummaryTone.OPENHANDS))

    tools = record.payload.get("tools")
    if isinstance(tools, list):
        tool_names = [
            tool["name"]
            for tool in tools
            if isinstance(tool, dict)
            and isinstance(tool.get("name"), str)
            and tool["name"]
        ]
        tools_lines = [f"AVAILABLE TOOLS ({len(tools)})"]
        tools_lines.extend(f"- {name}" for name in tool_names)
        if not tool_names:
            tools_lines.append("[No tool names recorded]")
        tools_section = "\n".join(tools_lines)
        lines.extend(("", tools_section))
        highlighted_sections.append((tools_section, SummaryTone.OPENHANDS))

    return _style_sections("\n".join(lines), highlighted_sections)


def _format_related_user_request(record: Record | None) -> str | None:
    if record is None or record.payload.get("source") != "user":
        return None
    raw_message = record.payload.get("llm_message")
    if not isinstance(raw_message, dict):
        return None
    try:
        message = Message.model_validate(raw_message)
    except ValidationError:
        return None
    return (
        f"USER REQUEST (SEQUENCE {record.sequence})\n{_format_content(message.content)}"
    )


def _style_sections(
    text: str, sections: list[tuple[str, SummaryTone]]
) -> StyledSummary:
    highlights = []
    search_start = 0
    for section, tone in sections:
        start = text.index(section, search_start)
        end = start + len(section)
        highlights.append(SummaryHighlight(start=start, end=end, tone=tone))
        search_start = end
    return StyledSummary(text=text, highlights=tuple(highlights))


def _format_content(content: Sequence[TextContent | ImageContent]) -> str:
    blocks = []
    for item in content:
        if isinstance(item, TextContent):
            blocks.append(item.text.strip() or "[Empty text block]")
        else:
            image_count = len(item.image_urls)
            noun = "image" if image_count == 1 else "images"
            blocks.append(f"[Image attachment: {image_count} {noun}]")
    return "\n\n".join(blocks) if blocks else "[No message content]"


def _parse_text_content(value: Any) -> TextContent | None:
    try:
        return TextContent.model_validate(value)
    except ValidationError:
        return None


def _format_text(text: str) -> str:
    return text.strip() or "[Empty text block]"


def _parse_extended_content(payload: dict[str, Any]) -> list[TextContent]:
    raw_content = payload.get("extended_content")
    if not isinstance(raw_content, list):
        return []
    parsed = []
    for item in raw_content:
        try:
            parsed.append(TextContent.model_validate(item))
        except ValidationError:
            continue
    return parsed


def _append_metadata(
    lines: list[str], payload: dict[str, Any], key: str, label: str
) -> None:
    value = payload.get(key)
    if isinstance(value, str) and value:
        lines.append(f"{label}: {value}")


def _metadata_value(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) and value else "unknown"
