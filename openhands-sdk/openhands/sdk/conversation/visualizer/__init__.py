from __future__ import annotations

from openhands.sdk.conversation.visualizer.base import (
    ConversationVisualizerBase,
)
from openhands.sdk.conversation.visualizer.default import (
    DefaultConversationVisualizer,
)


class ConversationVisualizer(DefaultConversationVisualizer):
    """Compatibility wrapper around DefaultConversationVisualizer.

    Allows constructing a visualizer with pre-existing ConversationStats for
    testing or ad-hoc usage, without a full Conversation state. In production
    usage, Conversation will call initialize(state) to attach stats.
    """

    def __init__(self, conversation_stats=None, **kwargs):
        super().__init__(**kwargs)
        self._direct_stats = conversation_stats

    @property
    def conversation_stats(self):
        # Prefer directly provided stats if available (used in tests)
        return self._direct_stats or super().conversation_stats


__all__ = [
    "ConversationVisualizerBase",
    "DefaultConversationVisualizer",
    "ConversationVisualizer",
]
