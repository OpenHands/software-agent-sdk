"""
Data models for multi-model PR review debate.

This module defines the data structures used throughout the debate workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReviewerModel(Enum):
    """Available reviewer models."""

    GPT_5_2 = "openai/gpt-5.2"
    CLAUDE_SONNET_4_5 = "anthropic/claude-sonnet-4-5-20250929"
    GEMINI_3_FLASH = "google/gemini-3-flash"

    @property
    def display_name(self) -> str:
        """Human-readable name for the model."""
        names = {
            ReviewerModel.GPT_5_2: "GPT-5.2",
            ReviewerModel.CLAUDE_SONNET_4_5: "Claude Sonnet 4.5",
            ReviewerModel.GEMINI_3_FLASH: "Gemini 3 Flash",
        }
        return names[self]


@dataclass
class PRInfo:
    """Information about a pull request."""

    number: str
    title: str
    body: str
    repo_name: str
    base_branch: str
    head_branch: str
    commit_id: str = ""
    diff: str = ""
    review_context: str = ""


@dataclass
class ReviewResult:
    """Result of a single model's review."""

    model: ReviewerModel
    review_text: str
    cost: float = 0.0
    token_usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass
class DebateMessage:
    """A message in the debate between reviewers."""

    sender: ReviewerModel
    recipient: ReviewerModel | None  # None means broadcast to all
    content: str
    round_number: int
    timestamp: float = 0.0


@dataclass
class DebateState:
    """State of the debate between reviewers."""

    initial_reviews: dict[ReviewerModel, str] = field(default_factory=dict)
    messages: list[DebateMessage] = field(default_factory=list)
    current_round: int = 0
    max_rounds: int = 3
    consensus_reached: bool = False
    final_review: str = ""

    def get_discussion_history(self) -> str:
        """Format the discussion history as a string."""
        if not self.messages:
            return "No messages yet."

        lines = []
        for msg in self.messages:
            sender_name = msg.sender.display_name
            if msg.recipient:
                recipient_name = msg.recipient.display_name
                header = f"**{sender_name}** → **{recipient_name}** (Round {msg.round_number})"  # noqa: E501
            else:
                header = f"**{sender_name}** → **All** (Round {msg.round_number})"

            lines.append(header)
            lines.append(msg.content)
            lines.append("")

        return "\n".join(lines)

    def add_message(
        self,
        sender: ReviewerModel,
        content: str,
        recipient: ReviewerModel | None = None,
    ) -> None:
        """Add a message to the debate."""
        import time

        self.messages.append(
            DebateMessage(
                sender=sender,
                recipient=recipient,
                content=content,
                round_number=self.current_round,
                timestamp=time.time(),
            )
        )


@dataclass
class DebateResult:
    """Final result of the debate process."""

    pr_info: PRInfo
    initial_reviews: dict[ReviewerModel, ReviewResult]
    debate_state: DebateState
    final_consolidated_review: str
    total_cost: float = 0.0
    total_tokens: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
