import json
import types
from typing import Annotated, Any, Union, get_args, get_origin

from openhands.sdk.tool import Action


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
                parsed_value = json.loads(value)
                # json.loads() returns dict, list, str, int, float, bool, or None
                # Only use parsed value if it matches expected collection types
                if isinstance(parsed_value, (list, dict)):
                    fixed_arguments[data_key] = parsed_value
            except (json.JSONDecodeError, ValueError):
                # If parsing fails, leave the original value
                # Pydantic will raise validation error if needed
                pass

    return fixed_arguments
