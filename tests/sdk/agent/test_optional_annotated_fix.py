"""Annotated[X, ...] | None fields must be repaired by fix_malformed_tool_arguments."""

from typing import Annotated

from pydantic import Field

from openhands.sdk.agent.utils import fix_malformed_tool_arguments
from openhands.sdk.tool.schema import Action


class OptionalAnnotatedAction(Action):
    """Action whose optional fields carry Annotated inside the union."""

    items: Annotated[list[int], Field(description="A list of items")] | None = None
    text: Annotated[str, Field(description="A string field")] | None = None


def test_optional_annotated_fields_are_repaired():
    fixed = fix_malformed_tool_arguments(
        {"items": "[1, 2]", "text": ["a", "b"]}, OptionalAnnotatedAction
    )

    assert fixed == {"items": [1, 2], "text": "ab"}
