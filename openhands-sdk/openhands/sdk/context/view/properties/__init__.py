from openhands.sdk.context.view.properties.base import ViewPropertyBase
from openhands.sdk.context.view.properties.batch_atomicity import BatchAtomicityProperty
from openhands.sdk.context.view.properties.tool_call_matching import (
    ToolCallMatchingProperty,
)
from openhands.sdk.context.view.properties.tool_loop_atomicity import (
    ToolLoopAtomicityProperty,
)
from openhands.sdk.context.view.properties.tool_result_uniqueness import (
    ToolResultUniquenessProperty,
)


ALL_PROPERTIES: list[ViewPropertyBase] = [
    BatchAtomicityProperty(),
    ToolCallMatchingProperty(),
    ToolLoopAtomicityProperty(),
    ToolResultUniquenessProperty(),
]
"""A list of all existing properties."""

__all__ = [
    "ViewPropertyBase",
    "BatchAtomicityProperty",
    "ToolCallMatchingProperty",
    "ToolLoopAtomicityProperty",
    "ToolResultUniquenessProperty",
    "ALL_PROPERTIES",
]
