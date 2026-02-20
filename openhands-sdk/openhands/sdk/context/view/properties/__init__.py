from openhands.sdk.context.view.properties.base import ViewPropertyBase
from openhands.sdk.context.view.properties.batch_atomicity import BatchAtomicityProperty
from openhands.sdk.context.view.properties.tool_call_matching import (
    ToolCallMatchingProperty,
)
from openhands.sdk.context.view.properties.tool_loop_atomicity import (
    ToolLoopAtomicityProperty,
)


ALL_PROPERTIES: list[ViewPropertyBase] = [
    BatchAtomicityProperty(),
    ToolCallMatchingProperty(),
    ToolLoopAtomicityProperty(),
]
"""A list of all existing properties."""

__all__ = [
    "ViewPropertyBase",
    "BatchAtomicityProperty",
    "ToolCallMatchingProperty",
    "ToolLoopAtomicityProperty",
    "ALL_PROPERTIES",
]
