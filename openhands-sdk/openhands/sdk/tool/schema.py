import logging
from abc import ABC
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from pydantic import ConfigDict, Field, create_model
from rich.text import Text

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.llm.message import content_to_str
from openhands.sdk.utils.models import (
    DiscriminatedUnionMixin,
)
from openhands.sdk.utils.visualize import display_dict


if TYPE_CHECKING:
    from typing import Self

logger = logging.getLogger(__name__)

S = TypeVar("S", bound="Schema")


def py_type(spec: dict[str, Any]) -> Any:
    """Map JSON schema types to Python types."""
    t = spec.get("type")

    # Normalize union types like ["string", "null"] to a single representative type.
    # MCP schemas often mark optional fields this way; we keep the non-null type.
    if isinstance(t, (list, tuple, set)):
        types = list(t)
        non_null = [tp for tp in types if tp != "null"]
        if len(non_null) == 1:
            t = non_null[0]
        else:
            return Any
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


def _shallow_expand_circular_ref(
    ref_def: dict[str, Any], ref_name: str
) -> dict[str, Any]:
    """Create a shallow expansion of a circular reference.

    Instead of returning a generic {"type": "object"}, this preserves immediate
    non-recursive properties while replacing recursive fields with generic objects.
    This gives LLMs more context about the structure.

    Args:
        ref_def: The definition of the referenced type.
        ref_name: The name of the reference (for detecting self-references).

    Returns:
        A shallow schema with immediate properties preserved.
    """
    result: dict[str, Any] = {"type": "object"}

    # Copy description if present
    if "description" in ref_def:
        result["description"] = ref_def["description"]

    # Process properties shallowly
    if "properties" in ref_def:
        result["properties"] = {}
        for prop_name, prop_schema in ref_def["properties"].items():
            # Check if this property references the circular type
            if _contains_ref_to(prop_schema, ref_name):
                # Replace recursive field with generic object
                shallow_prop: dict[str, Any] = {"type": "object"}
                if "description" in prop_schema:
                    shallow_prop["description"] = prop_schema["description"]
                # If it's an array of the recursive type, preserve array structure
                if prop_schema.get("type") == "array":
                    result["properties"][prop_name] = {
                        "type": "array",
                        "items": shallow_prop,
                    }
                    if "description" in prop_schema:
                        result["properties"][prop_name]["description"] = prop_schema[
                            "description"
                        ]
                else:
                    result["properties"][prop_name] = shallow_prop
            else:
                # Non-recursive property - copy as-is (shallow)
                result["properties"][prop_name] = _copy_simple_schema(prop_schema)

    # Copy required fields if present
    if "required" in ref_def:
        result["required"] = ref_def["required"]

    return result


def _contains_ref_to(schema: dict[str, Any], ref_name: str) -> bool:
    """Check if a schema contains a $ref to the given name."""
    if "$ref" in schema:
        return schema["$ref"] == f"#/$defs/{ref_name}"
    if "items" in schema:
        return _contains_ref_to(schema["items"], ref_name)
    if "anyOf" in schema:
        return any(_contains_ref_to(item, ref_name) for item in schema["anyOf"])
    return False


def _copy_simple_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy a simple schema without deep recursion."""
    result: dict[str, Any] = {}
    for key in ("type", "description", "enum"):
        if key in schema:
            result[key] = schema[key]
    # Handle anyOf for optional types
    if "anyOf" in schema:
        non_null = [t for t in schema["anyOf"] if t.get("type") != "null"]
        if non_null:
            result.update(_copy_simple_schema(non_null[0]))
    return result


def _process_schema_node(
    node: dict[str, Any],
    defs: dict[str, Any],
    _visiting: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Recursively process a schema node to simplify and resolve $ref.

    This function resolves JSON Schema $ref references and simplifies the schema
    structure for compatibility with MCP tool schemas. It handles circular
    references by tracking visited refs and stopping recursion when a cycle
    is detected.

    Args:
        node: The schema node to process.
        defs: The $defs dictionary containing reference definitions.
        _visiting: Internal parameter tracking refs currently being processed
            in the current recursion path to detect cycles.

    Returns:
        A simplified schema dict with $ref resolved (except for circular refs).

    Note:
        When a circular reference is detected, returns a shallow expansion of
        the referenced type with immediate non-recursive properties preserved,
        but recursive fields replaced with generic ``{"type": "object"}``.
        This prevents infinite recursion while preserving more type information
        than a fully generic fallback. Callers should be aware that recursive
        data types (trees, linked lists) will have simplified schemas that may
        not fully represent their nested structure.

    References:
        https://www.reddit.com/r/mcp/comments/1kjo9gt/toolinputschema_conversion_from_pydanticmodel/
        https://gist.github.com/leandromoreira/3de4819e4e4df9422d87f1d3e7465c16
    """
    if _visiting is None:
        _visiting = frozenset()

    # Handle $ref references
    if "$ref" in node:
        ref_path = node["$ref"]
        if ref_path.startswith("#/$defs/"):
            ref_name = ref_path.split("/")[-1]
            if ref_name in defs:
                # Check for circular reference - if we're already visiting this
                # ref in the current path, don't recurse (would cause infinite loop)
                if ref_name in _visiting:
                    logger.debug(
                        "Circular reference detected for '%s', using shallow expansion",
                        ref_name,
                    )
                    # Shallow expansion: include immediate properties but mark
                    # recursive fields as generic objects
                    return _shallow_expand_circular_ref(defs[ref_name], ref_name)

                # Add this ref to the visiting set for this recursion path
                new_visiting = _visiting | {ref_name}
                # Process the referenced definition
                return _process_schema_node(defs[ref_name], defs, new_visiting)

    # Start with a new schema object
    result: dict[str, Any] = {}

    # Copy the basic properties
    if "type" in node:
        result["type"] = node["type"]

    # Handle anyOf (often used for optional fields with None)
    if "anyOf" in node:
        non_null_types = [t for t in node["anyOf"] if t.get("type") != "null"]
        if non_null_types:
            # Process the first non-null type
            processed = _process_schema_node(non_null_types[0], defs, _visiting)
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
            result["properties"][prop_name] = _process_schema_node(
                prop_schema, defs, _visiting
            )

        # Add required fields if present
        if "required" in node:
            result["required"] = node["required"]

    # Handle arrays
    if node.get("type") == "array" and "items" in node:
        result["type"] = "array"
        result["items"] = _process_schema_node(node["items"], defs, _visiting)

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

        # Remove discriminator fields from properties (not for LLM)
        # Need to exclude both regular fields and computed fields (like 'kind')
        exclude_fields = set(DiscriminatedUnionMixin.model_fields.keys()) | set(
            DiscriminatedUnionMixin.model_computed_fields.keys()
        )
        for f in exclude_fields:
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

    ERROR_MESSAGE_HEADER: ClassVar[str] = "[An error occurred during execution.]\n"

    content: list[TextContent | ImageContent] = Field(
        default_factory=list,
        description=(
            "Content returned from the tool as a list of "
            "TextContent/ImageContent objects. "
            "When there is an error, it should be written in this field."
        ),
    )
    is_error: bool = Field(
        default=False, description="Whether the observation indicates an error"
    )

    @classmethod
    def from_text(
        cls,
        text: str,
        is_error: bool = False,
        **kwargs: Any,
    ) -> "Self":
        """Utility to create an Observation from a simple text string.

        Args:
            text: The text content to include in the observation.
            is_error: Whether this observation represents an error.
            **kwargs: Additional fields for the observation subclass.

        Returns:
            An Observation instance with the text wrapped in a TextContent.
        """
        return cls(content=[TextContent(text=text)], is_error=is_error, **kwargs)

    @property
    def text(self) -> str:
        """Extract all text content from the observation.

        Returns:
            Concatenated text from all TextContent items in content.
        """
        return "".join(
            item.text for item in self.content if isinstance(item, TextContent)
        )

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        """
        Default content formatting for converting observation to LLM readable content.
        Subclasses can override to provide richer content (e.g., images, diffs).
        """
        llm_content: list[TextContent | ImageContent] = []

        # If is_error is true, prepend error message
        if self.is_error:
            llm_content.append(TextContent(text=self.ERROR_MESSAGE_HEADER))

        # Add content (now always a list)
        llm_content.extend(self.content)

        return llm_content

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this observation.

        Subclasses can override for custom visualization; by default we show the
        same text that would be sent to the LLM.
        """
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")

        text_parts = content_to_str(self.to_llm_content)
        if text_parts:
            full_content = "".join(text_parts)
            text.append(full_content)
        else:
            text.append("[no text content]")
        return text
