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
    if record.kind == "llm.request":
        request_summary = _format_llm_request_summary(record)
        if request_summary is not None:
            return request_summary
    if record.kind == "llm.response":
        response_summary = _format_llm_response_summary(record)
        if response_summary is not None:
            return response_summary
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
    return _format_generic_summary(record)


def format_readable_data(value: Any) -> str:
    return "\n".join(_format_value_lines(value))


def format_llm_input(record: Record) -> str:
    request = record.payload.get("request")
    if not isinstance(request, dict):
        completion = record.payload.get("completion")
        if record.kind != "llm.response" or not isinstance(completion, dict):
            return "This activity is not an LLM request."
        result_keys = {
            "response",
            "raw_response",
            "error",
            "usage_summary",
            "cost",
            "timestamp",
            "latency_sec",
        }
        request = {
            key: value for key, value in completion.items() if key not in result_keys
        }
    if not request:
        return "No LLM request fields were captured."
    sections = _format_llm_request_sections(request)
    if not sections:
        return "No readable LLM request fields were captured."
    return "LLM REQUEST\n\n" + "\n\n".join(section for section, _ in sections)


def format_llm_output(record: Record) -> str:
    if record.kind != "llm.response":
        return "This activity is not an LLM response."
    summary = _format_llm_response_summary(record)
    if summary is None:
        return "No LLM output was captured."
    return summary.text


def _format_llm_request_summary(record: Record) -> StyledSummary | None:
    request = record.payload.get("request")
    if not isinstance(request, dict):
        return None
    lines = _record_metadata_lines(record, "LLM request")
    _append_value(lines, "File", record.payload.get("filename"))
    _append_value(lines, "LLM path", request.get("llm_path"))
    _append_value(lines, "Context window", request.get("context_window"))
    sections = _format_llm_request_sections(request)
    for section, _ in sections:
        lines.extend(("", section))
    return _style_sections("\n".join(lines), sections)


def _format_llm_request_sections(
    request: dict[str, Any],
) -> list[tuple[str, SummaryTone]]:
    sections: list[tuple[str, SummaryTone]] = []
    instructions = _extract_text(request.get("instructions"))
    if instructions:
        sections.append((f"SYSTEM INSTRUCTIONS\n{instructions}", SummaryTone.OPENHANDS))

    context_items = request.get("messages")
    if not isinstance(context_items, list):
        context_items = request.get("input")
    if isinstance(context_items, list):
        for index, item in enumerate(context_items, start=1):
            if not isinstance(item, dict):
                continue
            formatted = _format_llm_request_item(item, index)
            if formatted is not None:
                sections.append(formatted)

    tools = request.get("tools")
    if isinstance(tools, list):
        names = [
            name
            for tool in tools
            if isinstance(tool, dict) and (name := _request_tool_name(tool)) is not None
        ]
        tool_lines = [f"AVAILABLE TOOLS ({len(tools)})"]
        tool_lines.extend(f"- {name}" for name in names)
        if not names:
            tool_lines.append("[No tool names recorded]")
        sections.append(("\n".join(tool_lines), SummaryTone.OPENHANDS))
    return sections


def _format_llm_request_item(
    item: dict[str, Any], index: int
) -> tuple[str, SummaryTone] | None:
    item_type = item.get("type")
    role = item.get("role")
    if item_type == "message" or isinstance(role, str):
        normalized_role = role.upper() if isinstance(role, str) else "MESSAGE"
        text = _extract_text(item.get("content"))
        lines = [f"INPUT {index}: {normalized_role} MESSAGE"]
        lines.append(text or _non_text_content_summary(item.get("content")))
        tool_calls = item.get("tool_calls")
        if isinstance(tool_calls, list):
            calls = [call for call in tool_calls if isinstance(call, dict)]
            if calls:
                lines.extend(("", _format_tool_calls(calls)))
        tone = (
            SummaryTone.USER
            if role == "user"
            else SummaryTone.OPENHANDS
            if role in {"system", "developer", "tool"}
            else SummaryTone.AGENT
        )
        return "\n".join(lines), tone
    if item_type == "function_call":
        function = item.get("function")
        function = function if isinstance(function, dict) else item
        name = function.get("name")
        lines = [
            f"INPUT {index}: ASSISTANT TOOL CALL",
            f"Tool: {name if isinstance(name, str) else 'Unnamed tool'}",
        ]
        call_id = item.get("call_id") or item.get("id")
        if isinstance(call_id, str) and call_id:
            lines.append(f"Call ID: {call_id}")
        arguments = function.get("arguments")
        if arguments is not None:
            lines.append("Arguments:")
            lines.extend(_format_value_lines(_parse_structured_string(arguments), 2))
        return "\n".join(lines), SummaryTone.AGENT
    if item_type in {"function_call_output", "tool_result"}:
        lines = [f"INPUT {index}: TOOL RESULT"]
        call_id = item.get("call_id") or item.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            lines.append(f"Call ID: {call_id}")
        output = item.get("output", item.get("content"))
        lines.extend(_format_value_lines(_parse_structured_string(output)))
        return "\n".join(lines), SummaryTone.OPENHANDS
    if item_type == "reasoning":
        reasoning = _extract_text(item.get("summary") or item.get("content"))
        if reasoning:
            return f"INPUT {index}: MODEL REASONING\n{reasoning}", SummaryTone.AGENT
    return None


def _request_tool_name(tool: dict[str, Any]) -> str | None:
    function = tool.get("function")
    definition = function if isinstance(function, dict) else tool
    name = definition.get("name")
    return name if isinstance(name, str) and name else None


def _non_text_content_summary(content: Any) -> str:
    if not isinstance(content, list):
        return "[No text content]"
    content_types = [
        item_type
        for item in content
        if isinstance(item, dict) and isinstance((item_type := item.get("type")), str)
    ]
    if not content_types:
        return "[No text content]"
    return f"[Non-text content: {', '.join(content_types)}]"


def _format_llm_response_summary(record: Record) -> StyledSummary | None:
    completion = record.payload.get("completion", record.payload)
    if not isinstance(completion, dict):
        return None
    response = completion.get("response")
    error = completion.get("error")
    if not isinstance(response, dict) and not isinstance(error, dict):
        return None

    title = "LLM error" if isinstance(error, dict) else "LLM response"
    lines = _record_metadata_lines(record, title)
    _append_value(lines, "File", record.payload.get("filename"))
    if isinstance(response, dict):
        _append_value(lines, "Response ID", response.get("id"))
        _append_value(lines, "Model", response.get("model"))
        _append_value(lines, "Response type", response.get("object"))
        finish_reasons = _finish_reasons(response)
        if finish_reasons:
            lines.append(f"Finish reason: {', '.join(finish_reasons)}")
    _append_seconds(lines, "Latency", completion.get("latency_sec"))
    _append_cost(lines, completion.get("cost"))

    messages = completion.get("messages")
    input_items = completion.get("input")
    tools = completion.get("tools")
    request_lines = []
    if isinstance(messages, list):
        request_lines.append(f"Messages: {len(messages)}")
    if isinstance(input_items, list):
        request_lines.append(f"Input items: {len(input_items)}")
    if isinstance(tools, list):
        request_lines.append(f"Available tools: {len(tools)}")
    if request_lines:
        lines.extend(("", "REQUEST", *request_lines))

    highlighted_sections: list[tuple[str, SummaryTone]] = []
    if isinstance(error, dict):
        error_section = "PROVIDER ERROR\n" + format_readable_data(error)
        lines.extend(("", error_section))
        highlighted_sections.append((error_section, SummaryTone.AGENT))
    elif isinstance(response, dict):
        output_text, reasoning_text, tool_calls = _response_output(response)
        output_section = "MODEL OUTPUT\n" + (
            output_text or "[No assistant text returned]"
        )
        lines.extend(("", output_section))
        highlighted_sections.append((output_section, SummaryTone.AGENT))
        if reasoning_text:
            reasoning_section = f"MODEL REASONING\n{reasoning_text}"
            lines.extend(("", reasoning_section))
            highlighted_sections.append((reasoning_section, SummaryTone.AGENT))
        if tool_calls:
            tool_section = _format_tool_calls(tool_calls)
            lines.extend(("", tool_section))
            highlighted_sections.append((tool_section, SummaryTone.AGENT))

    usage = completion.get("usage_summary")
    if not isinstance(usage, dict) and isinstance(response, dict):
        candidate = response.get("usage")
        usage = candidate if isinstance(candidate, dict) else None
    if isinstance(usage, dict):
        usage_section = "TOKEN USAGE\n" + format_readable_data(usage)
        lines.extend(("", usage_section))

    return _style_sections("\n".join(lines), highlighted_sections)


def _format_generic_summary(record: Record) -> StyledSummary:
    event_type = record.payload.get("event_type")
    title_by_event = {
        "ActionEvent": "Tool action",
        "ObservationEvent": "Tool result",
        "ConversationStateUpdateEvent": "Conversation state update",
        "Condensation": "Context condensation",
    }
    title_by_kind = {
        "run.started": "Run started",
        "run.finished": "Run finished",
        "agent.started": "Agent started",
        "agent.finished": "Agent finished",
        "metrics.snapshot": "Metrics snapshot",
        "recorder.warning": "Recorder warning",
    }
    title = (
        title_by_event.get(event_type) if isinstance(event_type, str) else None
    ) or title_by_kind.get(record.kind, _field_label(record.kind))
    lines = _record_metadata_lines(record, title)
    metadata_keys = {
        "event_type",
        "id",
        "timestamp",
        "source",
        "kind",
        "llm_response_id",
    }
    details = {
        key: value for key, value in record.payload.items() if key not in metadata_keys
    }
    if details:
        lines.extend(("", "DETAILS", format_readable_data(details)))
    else:
        lines.extend(("", "[No additional details recorded]"))
    return StyledSummary(text="\n".join(lines))


def _record_metadata_lines(record: Record, title: str) -> list[str]:
    lines = [
        title,
        f"Kind: {record.kind}",
        f"Sequence: {record.sequence or 'pending'}",
        f"Agent: {record.agent_span_id or 'None'}",
    ]
    _append_value(lines, "Source", record.payload.get("source"))
    _append_value(lines, "Timestamp", record.payload.get("timestamp"))
    _append_value(lines, "Event ID", record.payload.get("id"))
    _append_value(lines, "LLM call ID", record.llm_call_id)
    response_id = record.llm_response_id or record.payload.get("llm_response_id")
    _append_value(lines, "LLM response ID", response_id)
    return lines


def _response_output(
    response: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]]]:
    output_parts = []
    reasoning_parts = []
    tool_calls = []
    choices = response.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            text = _extract_text(message.get("content"))
            if text:
                output_parts.append(text)
            reasoning = _extract_text(message.get("reasoning_content"))
            if reasoning:
                reasoning_parts.append(reasoning)
            calls = message.get("tool_calls")
            if isinstance(calls, list):
                tool_calls.extend(call for call in calls if isinstance(call, dict))

    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "function_call":
                tool_calls.append(item)
            elif item_type == "reasoning":
                reasoning = _extract_text(item.get("summary") or item.get("content"))
                if reasoning:
                    reasoning_parts.append(reasoning)
            else:
                text = _extract_text(item.get("content") or item.get("text"))
                if text:
                    output_parts.append(text)
    top_level_text = _extract_text(response.get("output_text"))
    if top_level_text:
        output_parts.append(top_level_text)
    return (
        "\n\n".join(dict.fromkeys(output_parts)),
        "\n\n".join(dict.fromkeys(reasoning_parts)),
        tool_calls,
    )


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_extract_text(item) for item in value]
        return "\n\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "output_text", "content", "value"):
            if key in value:
                text = _extract_text(value[key])
                if text:
                    return text
    return ""


def _format_tool_calls(tool_calls: list[dict[str, Any]]) -> str:
    lines = [f"TOOL CALLS ({len(tool_calls)})"]
    for index, tool_call in enumerate(tool_calls, start=1):
        function = tool_call.get("function")
        function = function if isinstance(function, dict) else tool_call
        name = function.get("name")
        lines.append(f"{index}. {name if isinstance(name, str) else 'Unnamed tool'}")
        call_id = tool_call.get("call_id") or tool_call.get("id")
        if isinstance(call_id, str) and call_id:
            lines.append(f"   Call ID: {call_id}")
        arguments = function.get("arguments")
        if arguments is not None:
            lines.append("   Arguments:")
            lines.extend(_format_value_lines(_parse_structured_string(arguments), 6))
    return "\n".join(lines)


def _finish_reasons(response: dict[str, Any]) -> list[str]:
    choices = response.get("choices")
    if not isinstance(choices, list):
        return []
    return list(
        dict.fromkeys(
            reason
            for choice in choices
            if isinstance(choice, dict)
            and isinstance((reason := choice.get("finish_reason")), str)
            and reason
        )
    )


def _format_value_lines(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return [prefix + "[None recorded]"]
        lines = []
        for key, item in value.items():
            item = _parse_structured_string(item) if key == "arguments" else item
            label = _field_label(str(key))
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{label}:")
                lines.extend(_format_value_lines(item, indent + 2))
            elif isinstance(item, str) and "\n" in item:
                lines.append(f"{prefix}{label}:")
                lines.extend(
                    f"{' ' * (indent + 2)}{line}" for line in item.splitlines()
                )
            else:
                lines.append(f"{prefix}{label}: {_format_scalar(item)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [prefix + "[None recorded]"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(prefix + "-")
                lines.extend(_format_value_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_format_scalar(item)}")
        return lines
    return [prefix + _format_scalar(value)]


def _parse_structured_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
    return parsed if isinstance(parsed, (dict, list)) else value


def _format_scalar(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value or "[Empty]"
    return str(value)


def _field_label(key: str) -> str:
    words = key.replace(".", " ").replace("_", " ").split()
    labels = []
    for word in words:
        lowered = word.lower()
        if word.isupper():
            labels.append(word)
        elif lowered in {"id", "llm", "api", "url", "ui"}:
            labels.append(lowered.upper())
        else:
            labels.append(word.capitalize())
    return " ".join(labels) or "Details"


def _append_value(lines: list[str], label: str, value: Any) -> None:
    if value is not None and value != "":
        lines.append(f"{label}: {_format_scalar(value)}")


def _append_seconds(lines: list[str], label: str, value: Any) -> None:
    if isinstance(value, int | float):
        lines.append(f"{label}: {value:.3f}s")


def _append_cost(lines: list[str], value: Any) -> None:
    if isinstance(value, int | float):
        lines.append(f"Cost: ${value:.6f}")


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
