import json
import types
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Annotated, Any, ClassVar, TypeVar, Union, get_args, get_origin

from pydantic import ConfigDict, Field, create_model, model_validator
from rich.text import Text

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.llm.message import content_to_str
from openhands.sdk.utils.models import (
    DiscriminatedUnionMixin,
)
from openhands.sdk.utils.visualize import display_dict


S = TypeVar("S", bound="Schema")


def py_type(spec: dict[str, Any]) -> Any:
    """Map JSON schema types to Python types."""
    t = spec.get("type")
    if t == "array":
        items = spec.get("items", {})
        inner = py_type(items) if isinstance(items, dict) else Any
        return list[inner]  # type: ignore[index]
    if t == "object":
        return dict[str, Any]
    _map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
    }
    if t in _map:
        return _map[t]
    return Any


def _process_schema_node(node, defs):
    """Recursively process a schema node to simplify and resolve $ref.

    https://www.reddit.com/r/mcp/comments/1kjo9gt/toolinputschema_conversion_from_pydanticmodel/
    https://gist.github.com/leandromoreira/3de4819e4e4df9422d87f1d3e7465c16
    """
    # Handle $ref references
    if "$ref" in node:
        ref_path = node["$ref"]
        if ref_path.startswith("#/$defs/"):
            ref_name = ref_path.split("/")[-1]
            if ref_name in defs:
                # Process the referenced definition
                return _process_schema_node(defs[ref_name], defs)

    # Start with a new schema object
    result = {}

    # Copy the basic properties
    if "type" in node:
        result["type"] = node["type"]

    # Handle anyOf (often used for optional fields with None)
    if "anyOf" in node:
        non_null_types = [t for t in node["anyOf"] if t.get("type") != "null"]
        if non_null_types:
            # Process the first non-null type
            processed = _process_schema_node(non_null_types[0], defs)
            result.update(processed)

    # Handle description
    if "description" in node:
        result["description"] = node["description"]

    # Handle object properties recursively
    if node.get("type") == "object" and "properties" in node:
        result["type"] = "object"
        result["properties"] = {}

        # Process each property
        for prop_name, prop_schema in node["properties"].items():
            result["properties"][prop_name] = _process_schema_node(prop_schema, defs)

        # Add required fields if present
        if "required" in node:
            result["required"] = node["required"]

    # Handle arrays
    if node.get("type") == "array" and "items" in node:
        result["type"] = "array"
        result["items"] = _process_schema_node(node["items"], defs)

    # Handle enum
    if "enum" in node:
        result["enum"] = node["enum"]

    return result


class Schema(DiscriminatedUnionMixin):
    """Base schema for input action / output observation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _decode_json_strings(cls, data: Any) -> Any:
        """Pre-validator that automatically decodes JSON strings for list/dict fields.

        This validator runs before field validation and checks if any field that
        expects a list or dict type has received a JSON string instead. If so,
        it automatically decodes the string using json.loads().

        This handles cases where certain LLMs (such as GLM 4.6) incorrectly encode
        array/object parameters as JSON strings when using native function calling.

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
        This validator automatically fixes this by detecting that view_range
        expects a list type and decoding the JSON string to get the actual array.

        Args:
            data: The input data (usually a dict) before validation.

        Returns:
            The data with JSON strings decoded where appropriate.
        """
        if not isinstance(data, dict):
            return data

        # Use model_fields to properly handle aliases and inherited fields
        for field_name, field_info in cls.model_fields.items():
            # Check both the field name and its alias (if any)
            data_key = field_info.alias if field_info.alias else field_name
            if data_key not in data:
                continue

            value = data[data_key]
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
            if origin is Union or (
                hasattr(types, "UnionType") and origin is types.UnionType
            ):
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
                        data[data_key] = parsed_value
                except (json.JSONDecodeError, ValueError):
                    # If parsing fails, leave the original value
                    # Pydantic will raise validation error if needed
                    pass

        return data

    @classmethod
    def to_mcp_schema(cls) -> dict[str, Any]:
        """Convert to JSON schema format compatible with MCP."""
        full_schema = cls.model_json_schema()
        # This will get rid of all "anyOf" in the schema,
        # so it is fully compatible with MCP tool schema
        result = _process_schema_node(full_schema, full_schema.get("$defs", {}))

        # Remove 'kind' from properties if present (discriminator field, not for LLM)
        EXCLUDE_FIELDS = DiscriminatedUnionMixin.model_fields.keys()
        for f in EXCLUDE_FIELDS:
            if "properties" in result and f in result["properties"]:
                result["properties"].pop(f)
                # Also remove from required if present
                if "required" in result and f in result["required"]:
                    result["required"].remove(f)

        return result

    @classmethod
    def from_mcp_schema(
        cls: type[S], model_name: str, schema: dict[str, Any]
    ) -> type["S"]:
        """Create a Schema subclass from an MCP/JSON Schema object.

        For non-required fields, we annotate as `T | None`
        so explicit nulls are allowed.
        """
        assert isinstance(schema, dict), "Schema must be a dict"
        assert schema.get("type") == "object", "Only object schemas are supported"

        props: dict[str, Any] = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])

        fields: dict[str, tuple] = {}
        for fname, spec in props.items():
            spec = spec if isinstance(spec, dict) else {}
            tp = py_type(spec)

            # Add description if present
            desc: str | None = spec.get("description")

            # Required → bare type, ellipsis sentinel
            # Optional → make nullable via `| None`, default None
            if fname in required:
                anno = tp
                default = ...
            else:
                anno = tp | None  # allow explicit null in addition to omission
                default = None

            fields[fname] = (
                anno,
                Field(default=default, description=desc)
                if desc
                else Field(default=default),
            )

        return create_model(model_name, __base__=cls, **fields)  # type: ignore[return-value]


class Action(Schema, ABC):
    """Base schema for input action."""

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this action.

        This method can be overridden by subclasses to customize visualization.
        The base implementation displays all action fields systematically.
        """
        content = Text()

        # Display action name
        action_name = self.__class__.__name__
        content.append("Action: ", style="bold")
        content.append(action_name)
        content.append("\n\n")

        # Display all action fields systematically
        content.append("Arguments:", style="bold")
        action_fields = self.model_dump()
        content.append(display_dict(action_fields))

        return content


class Observation(Schema, ABC):
    """Base schema for output observation."""

    @property
    @abstractmethod
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        """Get the observation string to show to the agent."""

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this action.

        This method can be overridden by subclasses to customize visualization.
        The base implementation displays all action fields systematically.
        """
        content = Text()
        text_parts = content_to_str(self.to_llm_content)
        if text_parts:
            full_content = "".join(text_parts)
            content.append(full_content)
        else:
            content.append("[no text content]")
        return content
