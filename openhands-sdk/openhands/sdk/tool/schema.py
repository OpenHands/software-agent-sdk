from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar, TypeVar

from pydantic import ConfigDict, Field, create_model
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
