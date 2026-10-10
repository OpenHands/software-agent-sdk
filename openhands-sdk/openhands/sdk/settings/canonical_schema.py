"""Structural settings facts read from canonical Pydantic JSON Schema."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel


# Presentation annotations may carry only these keys. Structural facts
# (type, default, constraints, enum) stay on the JSON Schema property.
PRESENTATION_METADATA_KEYS = frozenset({"depends_on", "label", "prominence", "variant"})

_JSON_VALUE_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "array", "object"}
)


def schema_properties(model: type[BaseModel]) -> dict[str, dict[str, Any]]:
    """Return the model's JSON Schema properties, keyed by field name."""
    properties = model.model_json_schema().get("properties", {})
    return {name: prop for name, prop in properties.items() if isinstance(prop, dict)}


def union_variant(model: type[BaseModel]) -> tuple[str, str] | None:
    """Return ``(selector_field, variant_id)`` for a single string ``const``.

    Condenser variants identify themselves with ``condenser_kind``'s ``const``.
    Models with no single string const are not union variants.
    """
    consts = [
        (name, prop["const"])
        for name, prop in schema_properties(model).items()
        if isinstance(prop.get("const"), str)
    ]
    if len(consts) != 1:
        return None
    return consts[0]


def structural_facts(
    prop: Mapping[str, Any],
    *,
    fallback_default: Any,
    closed_choices: bool = True,
) -> dict[str, Any]:
    """Read type, default, choices, secret-ness, and numeric bounds.

    ``default`` comes from the JSON Schema property when Pydantic emitted one.
    ``default_factory`` values are omitted from JSON Schema, so callers pass
    the already JSON-normalized factory result as ``fallback_default``.

    Pass ``closed_choices=False`` when the annotation accepts values outside
    its ``enum`` (for example ``Literal[...] | SkipJsonSchema[str]``); JSON
    Schema erases that open branch, so it cannot be detected here.
    """
    branches = _non_null_branches(prop)
    choice_values = _choice_values(prop, branches) if closed_choices else []
    return {
        "value_type": _value_type(branches, choice_values),
        "default": prop["default"] if "default" in prop else fallback_default,
        "secret": _is_secret(branches),
        "choices": [(value, str(value)) for value in choice_values],
        "minimum": _numeric_constraint(branches, "minimum"),
        "exclusive_minimum": _numeric_constraint(branches, "exclusiveMinimum"),
        "maximum": _numeric_constraint(branches, "maximum"),
        "exclusive_maximum": _numeric_constraint(branches, "exclusiveMaximum"),
        "min_length": _int_constraint(branches, "minLength"),
        "max_length": _int_constraint(branches, "maxLength"),
    }


def _non_null_branches(prop: Mapping[str, Any]) -> list[dict[str, Any]]:
    options = prop.get("anyOf") or prop.get("oneOf")
    if isinstance(options, list):
        branches: list[dict[str, Any]] = []
        for option in options:
            if isinstance(option, dict) and option.get("type") != "null":
                branches.append(option)
        if branches:
            return branches
    if isinstance(prop, dict):
        return [prop]
    return [dict(prop)]


def _choice_values(
    prop: Mapping[str, Any],
    branches: Sequence[Mapping[str, Any]],
) -> list[Any]:
    values: list[Any] = []
    seen: set[tuple[str, Any]] = set()
    for node in (prop, *branches):
        raw: list[Any] = []
        if "const" in node:
            raw.append(node["const"])
        enum = node.get("enum")
        if isinstance(enum, list):
            raw.extend(enum)
        for value in raw:
            if not _is_choice_value(value):
                continue
            key = ("bool" if isinstance(value, bool) else type(value).__name__, value)
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
    return values


def _is_choice_value(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    return isinstance(value, (int, float, str))


def _value_type(branches: Sequence[Mapping[str, Any]], choices: list[Any]) -> str:
    if choices:
        if all(isinstance(value, bool) for value in choices):
            return "boolean"
        if all(
            isinstance(value, int) and not isinstance(value, bool) for value in choices
        ):
            return "integer"
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in choices
        ):
            return "number"
        return "string"
    for node in branches:
        json_type = node.get("type")
        if json_type in _JSON_VALUE_TYPES:
            return str(json_type)
        if "items" in node:
            return "array"
        if "additionalProperties" in node or "$ref" in node:
            return "object"
    return "string"


def _is_secret(branches: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        node.get("format") == "password" or node.get("writeOnly") is True
        for node in branches
    )


def _numeric_constraint(
    branches: Sequence[Mapping[str, Any]], key: str
) -> int | float | None:
    for node in branches:
        if key not in node:
            continue
        value = node[key]
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
    return None


def _int_constraint(branches: Sequence[Mapping[str, Any]], key: str) -> int | None:
    for node in branches:
        value = node.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None
