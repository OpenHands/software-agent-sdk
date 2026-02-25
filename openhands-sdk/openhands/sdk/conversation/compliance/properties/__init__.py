"""Concrete implementations of API compliance properties.

Each property corresponds to one or more API compliance patterns:
- InterleavedMessageProperty: a01, a03, a04, a07
- UnmatchedToolResultProperty: a02, a06
- DuplicateToolResultProperty: a05
- ToolResultOrderProperty: a08
"""

from openhands.sdk.conversation.compliance.base import APICompliancePropertyBase
from openhands.sdk.conversation.compliance.properties.duplicate_tool_result import (
    DuplicateToolResultProperty,
)
from openhands.sdk.conversation.compliance.properties.interleaved_message import (
    InterleavedMessageProperty,
)
from openhands.sdk.conversation.compliance.properties.tool_result_order import (
    ToolResultOrderProperty,
)
from openhands.sdk.conversation.compliance.properties.unmatched_tool_result import (
    UnmatchedToolResultProperty,
)


# All properties in recommended check order
ALL_COMPLIANCE_PROPERTIES: list[APICompliancePropertyBase] = [
    ToolResultOrderProperty(),
    UnmatchedToolResultProperty(),
    DuplicateToolResultProperty(),
    InterleavedMessageProperty(),
]

__all__ = [
    "ALL_COMPLIANCE_PROPERTIES",
    "DuplicateToolResultProperty",
    "InterleavedMessageProperty",
    "ToolResultOrderProperty",
    "UnmatchedToolResultProperty",
]
