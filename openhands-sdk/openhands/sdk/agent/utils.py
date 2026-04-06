import json
import re
import shlex
import types
from collections.abc import Collection, Sequence
from typing import (
    Annotated,
    Any,
    Union,
    get_args,
    get_origin,
    overload,
)

from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.context.view import View
from openhands.sdk.conversation.types import ConversationTokenCallbackType
from openhands.sdk.event.base import Event, LLMConvertibleEvent
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM, LLMResponse, Message
from openhands.sdk.tool import Action, ToolDefinition


# Regex matching raw ASCII control characters (U+0000–U+001F) that are
# illegal inside JSON strings per RFC 8259 §7.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f]")

# Mapping from raw control-char ordinals to their JSON-legal two-character
# escape sequences.  Characters without a short alias fall back to \uXXXX.
_CTRL_ESCAPE_TABLE: dict[int, str] = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
}


def _escape_control_char(m: re.Match[str]) -> str:
    """Replace a single raw control character with its JSON escape."""
    ch = m.group(0)
    return _CTRL_ESCAPE_TABLE.get(ord(ch), f"\\u{ord(ch):04x}")


def sanitize_json_control_chars(raw: str) -> str:
    """Escape raw control characters in a JSON string produced by an LLM.

    Some models (e.g. kimi-k2.5, minimax-m2.5) emit literal control
    characters (newline, tab, …) inside ``tool_call.arguments`` instead of
    their proper two-character JSON escape sequences (``\\n``, ``\\t``, …).
    ``json.loads`` rejects these per RFC 8259.

    This function replaces every raw U+0000–U+001F byte with the correct
    escape sequence so the string becomes valid JSON.
    """
    return _CONTROL_CHAR_RE.sub(_escape_control_char, raw)


def fix_malformed_tool_arguments(
    arguments: dict[str, Any], action_type: type[Action]
) -> dict[str, Any]:
    """Fix malformed tool arguments by decoding JSON strings for list/dict fields.

    This function handles cases where certain LLMs (such as GLM 4.6) incorrectly
    encode array/object parameters as JSON strings when using native function calling.

    Example raw LLM output from GLM 4.6:
    {
        "role": "assistant",
        "content": "I'll view the file for you.",
        "tool_calls": [{
            "id": "call_ef8e",
            "type": "function",
            "function": {
                "name": "str_replace_editor",
                "arguments": '{
                    "command": "view",
                    "path": "/tmp/test.txt",
                    "view_range": "[1, 5]"
                }'
            }
        }]
    }

    Expected output: `"view_range" : [1, 5]`

    Note: The arguments field is a JSON string. When decoded, view_range is
    incorrectly a string "[1, 5]" instead of the proper array [1, 5].
    This function automatically fixes this by detecting that view_range
    expects a list type and decoding the JSON string to get the actual array.

    Args:
        arguments: The parsed arguments dict from json.loads(tool_call.arguments).
        action_type: The action type that defines the expected schema.

    Returns:
        The arguments dict with JSON strings decoded where appropriate.
    """
    if not isinstance(arguments, dict):
        return arguments

    fixed_arguments = arguments.copy()

    # Use model_fields to properly handle aliases and inherited fields
    for field_name, field_info in action_type.model_fields.items():
        # Check both the field name and its alias (if any)
        data_key = field_info.alias if field_info.alias else field_name
        if data_key not in fixed_arguments:
            continue

        value = fixed_arguments[data_key]
        # Skip if value is not a string
        if not isinstance(value, str):
            continue

        expected_type = field_info.annotation

        # Unwrap Annotated types - only the first arg is the actual type
        if get_origin(expected_type) is Annotated:
            type_args = get_args(expected_type)
            expected_type = type_args[0] if type_args else expected_type

        # Get the origin of the expected type (e.g., list from list[str])
        origin = get_origin(expected_type)

        # For Union types, we need to check all union members
        if origin is Union or origin is types.UnionType:
            # For Union types, check each union member
            type_args = get_args(expected_type)
            expected_origins = [get_origin(arg) or arg for arg in type_args]
        else:
            # For non-Union types, just check the origin
            expected_origins = [origin or expected_type]

        # Check if any of the expected types is list or dict
        if any(exp in (list, dict) for exp in expected_origins):
            # Try to parse the string as JSON
            try:
                # `strict=False` allows control characters (e.g. newlines) that
                # the outer json.loads decoded from escape sequences.
                # https://docs.python.org/3/library/json.html#json.JSONDecoder
                parsed_value = json.loads(value, strict=False)
                # json.loads() returns dict, list, str, int, float, bool, or None
                # Only use parsed value if it matches expected collection types
                if isinstance(parsed_value, (list, dict)):
                    fixed_arguments[data_key] = parsed_value
            except (json.JSONDecodeError, ValueError):
                # If parsing fails, leave the original value
                # Pydantic will raise validation error if needed
                pass
    return fixed_arguments


TOOL_NAME_ALIASES: dict[str, str] = {
    "bash": "terminal",
    "command": "terminal",
    "execute": "terminal",
    "execute_bash": "terminal",
    "str_replace": "file_editor",
    "str_replace_editor": "file_editor",
}

# This fallback is intentionally tiny: it only accepts exact, bare command names
# that are useful as read-only defaults when some models emit them as tool names.
_SHELL_TOOL_FALLBACK_COMMANDS = frozenset({"find", "ls", "pwd"})

# Typo normalization for common mistakes in security_risk field
_SECURITY_RISK_TYPOS = {"security_rort", "securtiy_risk", "security_riks"}


def _normalize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Normalize common typos and inconsistencies in tool arguments."""
    normalized = arguments.copy()

    # Fix security_risk typos
    for typo in _SECURITY_RISK_TYPOS:
        if typo in normalized:
            normalized["security_risk"] = normalized.pop(typo)
            break

    # Remove any arguments that are clearly not valid (None values, etc.)
    # but keep all others to preserve tool-specific arguments
    return {k: v for k, v in normalized.items() if v is not None}


def parse_tool_call_arguments(raw_arguments: str) -> dict[str, Any]:
    """Parse tool call arguments, sanitizing raw control chars only on fallback."""
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        sanitized_args = sanitize_json_control_chars(raw_arguments)
        parsed = json.loads(sanitized_args)

    result = parsed if isinstance(parsed, dict) else {}
    return _normalize_arguments(result)


def _infer_file_editor_command(arguments: dict[str, Any]) -> str | None:
    if "command" in arguments:
        return None
    if "old_str" in arguments:
        return "str_replace"
    if "insert_line" in arguments:
        return "insert"
    if "file_text" in arguments:
        return "create"
    if "path" in arguments:
        return "view"
    return None


def _has_file_editor_hint(arguments: dict[str, Any]) -> bool:
    """Check if arguments contain any hint that this is a file_editor call."""
    file_editor_hints = frozenset(
        {
            "old_str",
            "new_str",
            "insert_line",
            "file_text",
            "path",
            "view_range",
        }
    )
    return bool(arguments and any(k in arguments for k in file_editor_hints))


def _build_grep_terminal_command(arguments: dict[str, Any]) -> str | None:
    """Return a safe terminal command for structured grep fallbacks.

    Returning ``None`` keeps malformed grep payloads on the normal "tool not
    found" path instead of broadening terminal execution.
    """
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return None

    command_parts = ["grep", "-RIn"]
    include = arguments.get("include")
    if isinstance(include, str) and include.strip():
        command_parts.extend(["--include", include])

    command_parts.extend(["--", pattern])

    path = arguments.get("path")
    command_parts.append(path if isinstance(path, str) and path.strip() else ".")
    return shlex.join(command_parts)


def _maybe_rewrite_as_terminal_command(
    tool_name: str,
    arguments: dict[str, Any],
) -> str | None:
    """Return a narrow terminal fallback for shell-style tool names.

    Aliases are handled before this helper, so Anthropic-style names like
    ``str_replace`` normalize to canonical SDK tools instead of being treated as
    shell commands. This helper only runs for otherwise-unknown names when the
    agent already exposes ``terminal``.
    """
    if tool_name == "grep":
        return _build_grep_terminal_command(arguments)

    if arguments or tool_name not in _SHELL_TOOL_FALLBACK_COMMANDS:
        return None

    return tool_name


def normalize_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    available_tools: Collection[str],
) -> tuple[str, dict[str, Any]]:
    """Normalize legacy tool names and Anthropic-style argument shapes.

    Precedence is intentional: legacy aliases resolve first, terminal fallback
    only applies to still-unknown names, and file_editor command inference runs
    after the canonical tool name is known.
    """
    normalized_tool_name = tool_name
    normalized_arguments = arguments.copy()

    alias_target = TOOL_NAME_ALIASES.get(tool_name)
    if alias_target and alias_target in available_tools:
        normalized_tool_name = alias_target
    elif tool_name not in available_tools and "terminal" in available_tools:
        terminal_command = _maybe_rewrite_as_terminal_command(
            tool_name,
            normalized_arguments,
        )
        if terminal_command is not None:
            normalized_tool_name = "terminal"
            # Preserve only terminal-relevant arguments (security_risk, summary)
            # along with the generated command
            normalized_arguments = {
                key: value
                for key, value in normalized_arguments.items()
                if key in {"security_risk", "summary"}
            }
            normalized_arguments["command"] = terminal_command

    if normalized_tool_name == "file_editor":
        inferred_command = _infer_file_editor_command(normalized_arguments)
        if inferred_command is not None:
            normalized_arguments = {
                "command": inferred_command,
                **normalized_arguments,
            }
        elif not normalized_arguments or (
            "command" not in normalized_arguments
            and not _has_file_editor_hint(normalized_arguments)
        ):
            raise ValueError(
                f"Cannot infer 'command' for tool '{normalized_tool_name}' "
                f"from empty arguments {normalized_arguments!r}. "
                f"Expected one of: str_replace, insert, create, view with "
                f"appropriate arguments (e.g., old_str for str_replace, "
                f"path for view)."
            )

    return normalized_tool_name, normalized_arguments


@overload
def prepare_llm_messages(
    events: Sequence[Event],
    condenser: None = None,
    additional_messages: list[Message] | None = None,
    llm: LLM | None = None,
) -> list[Message]: ...


@overload
def prepare_llm_messages(
    events: Sequence[Event],
    condenser: CondenserBase,
    additional_messages: list[Message] | None = None,
    llm: LLM | None = None,
) -> list[Message] | Condensation: ...


def prepare_llm_messages(
    events: Sequence[Event],
    condenser: CondenserBase | None = None,
    additional_messages: list[Message] | None = None,
    llm: LLM | None = None,
) -> list[Message] | Condensation:
    """Prepare LLM messages from conversation context.

    This utility function extracts the common logic for preparing conversation
    context that is shared between agent.step() and ask_agent() methods.
    It handles condensation internally and calls the callback when needed.

    Args:
        events: Sequence of events to prepare messages from
        condenser: Optional condenser for handling context window limits
        additional_messages: Optional additional messages to append
        llm: Optional LLM instance from the agent, passed to condenser for
            token counting or other LLM features

    Returns:
        List of messages ready for LLM completion, or a Condensation event
        if condensation is needed

    Raises:
        RuntimeError: If condensation is needed but no callback is provided
    """

    view = View.from_events(events)
    llm_convertible_events: list[LLMConvertibleEvent] = view.events

    # If a condenser is registered, we need to give it an
    # opportunity to transform the events. This will either
    # produce a list of events, exactly as expected, or a
    # new condensation that needs to be processed
    if condenser is not None:
        condensation_result = condenser.condense(view, agent_llm=llm)

        match condensation_result:
            case View():
                llm_convertible_events = condensation_result.events

            case Condensation():
                return condensation_result

    # Convert events to messages
    messages = LLMConvertibleEvent.events_to_messages(llm_convertible_events)

    # Add any additional messages (e.g., user question for ask_agent)
    if additional_messages:
        messages.extend(additional_messages)

    return messages


def make_llm_completion(
    llm: LLM,
    messages: list[Message],
    tools: list[ToolDefinition] | None = None,
    on_token: ConversationTokenCallbackType | None = None,
) -> LLMResponse:
    """Make an LLM completion call with the provided messages and tools.

    Args:
        llm: The LLM instance to use for completion
        messages: The messages to send to the LLM
        tools: Optional list of tools to provide to the LLM
        on_token: Optional callback for streaming token updates

    Returns:
        LLMResponse from the LLM completion call

    Note:
        Always exposes a 'security_risk' parameter in tool schemas via
        add_security_risk_prediction=True. This ensures the schema remains
        consistent, even if the security analyzer is disabled. Validation of
        this field happens dynamically at runtime depending on the analyzer
        configured. This allows weaker models to omit risk field and bypass
        validation requirements when analyzer is disabled. For detailed logic,
        see `_extract_security_risk` method in agent.py.

        Summary field is always added to tool schemas for transparency and
        explainability of agent actions.
    """
    if llm.uses_responses_api():
        return llm.responses(
            messages=messages,
            tools=tools or [],
            include=None,
            store=False,
            add_security_risk_prediction=True,
            on_token=on_token,
        )
    else:
        return llm.completion(
            messages=messages,
            tools=tools or [],
            add_security_risk_prediction=True,
            on_token=on_token,
        )
