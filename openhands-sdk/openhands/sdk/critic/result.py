from typing import Any, ClassVar

from pydantic import BaseModel, Field
from rich.text import Text


class CriticResult(BaseModel):
    """A critic result is a score and a message."""

    THRESHOLD: ClassVar[float] = 0.5
    DISPLAY_THRESHOLD: ClassVar[float] = 0.2  # Only show scores above this threshold

    score: float = Field(
        description="A predicted probability of success between 0 and 1.",
        ge=0.0,
        le=1.0,
    )
    message: str | None = Field(description="An optional message explaining the score.")
    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional metadata about the critic evaluation. "
            "Can include event_ids and categorized_features for visualization."
        ),
    )

    @property
    def success(self) -> bool:
        """Whether the agent is successful."""
        return self.score >= CriticResult.THRESHOLD

    @staticmethod
    def _get_quality_assessment(score: float) -> tuple[str, str, str]:
        """Convert score to human-readable quality assessment.

        Returns:
            Tuple of (label, emoji, style)
        """
        if score >= 0.8:
            return "likely successful", "✓", "green bold"
        elif score >= 0.6:
            return "probably successful", "✓", "green"
        elif score >= 0.5:
            return "might need some adjustments", "~", "yellow"
        elif score >= 0.3:
            return "likely needs more work", "!", "yellow"
        else:
            return "probably unsuccessful", "✗", "red"

    @staticmethod
    def _get_confidence_label(prob: float) -> tuple[str, str]:
        """Convert probability to confidence level label and style.

        Returns:
            Tuple of (confidence_label, style)
        """
        if prob >= 0.7:
            return "High", "red bold"
        elif prob >= 0.5:
            return "Medium", "yellow"
        else:
            return "Low", "dim"

    @staticmethod
    def _get_sentiment_emoji(sentiment: str) -> str:
        """Get emoji for sentiment type."""
        emoji_map = {
            "Positive": "😊",
            "Neutral": "😐",
            "Negative": "😟",
        }
        return emoji_map.get(sentiment, "")

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of the critic result."""
        content = Text()
        content.append("\n\nCritic thinks the task is ", style="bold")

        # Display main score as human-readable quality with emoji
        label, emoji, style = self._get_quality_assessment(self.score)
        content.append(f"{emoji} {label}", style=style)

        # Use categorized features from metadata if available
        if self.metadata and "categorized_features" in self.metadata:
            categorized = self.metadata["categorized_features"]
            self._append_sentiment(content, categorized)
            self._append_categorized_features(content, categorized)
        else:
            # Fallback: display message as-is
            if self.message:
                content.append(f"\n  {self.message}\n")
            else:
                content.append("\n")

        return content

    def _append_sentiment(self, content: Text, categorized: dict[str, Any]) -> None:
        """Append sentiment information to content."""
        sentiment = categorized.get("sentiment")
        if not sentiment:
            return

        content.append(" | ", style="dim")
        content.append("Expected User Response: ", style="bold")

        predicted = sentiment.get("predicted", "")
        emoji = self._get_sentiment_emoji(predicted)

        # Color sentiment based on type
        if predicted == "Positive":
            sentiment_style = "green"
        elif predicted == "Negative":
            sentiment_style = "red"
        else:  # Neutral
            sentiment_style = "yellow"

        content.append(f"{emoji} {predicted}", style=sentiment_style)

    def _append_categorized_features(
        self, content: Text, categorized: dict[str, Any]
    ) -> None:
        """Append categorized features to content, each category on its own line."""
        has_content = False

        # Agent behavioral issues
        agent_issues = categorized.get("agent_behavioral_issues", [])
        if agent_issues:
            content.append("\n  ")
            content.append("⚠ Potential Issues: ", style="bold yellow")
            self._append_feature_list_inline(content, agent_issues)
            has_content = True

        # User follow-up patterns
        user_patterns = categorized.get("user_followup_patterns", [])
        if user_patterns:
            content.append("\n  ")
            content.append("📝 Likely Follow-up: ", style="bold")
            self._append_feature_list_inline(content, user_patterns)
            has_content = True

        # Infrastructure issues
        infra_issues = categorized.get("infrastructure_issues", [])
        if infra_issues:
            content.append("\n  ")
            content.append("🔧 Infrastructure: ", style="bold")
            self._append_feature_list_inline(content, infra_issues)
            has_content = True

        # Other metrics
        other = categorized.get("other", [])
        if other:
            content.append("\n  ")
            content.append("Other: ", style="bold dim")
            self._append_feature_list_inline(content, other, is_other=True)
            has_content = True

        if not has_content:
            content.append("\n")
        else:
            content.append("\n")

    def _append_feature_list_inline(
        self,
        content: Text,
        features: list[dict[str, Any]],
        is_other: bool = False,
    ) -> None:
        """Append features inline with confidence levels instead of raw probabilities."""
        for i, feature in enumerate(features):
            display_name = feature.get("display_name", feature.get("name", "Unknown"))
            prob = feature.get("probability", 0.0)

            # Get confidence label and style
            if is_other:
                confidence_label, confidence_style = "—", "white"
            else:
                confidence_label, confidence_style = self._get_confidence_label(prob)

            # Add dot separator between features
            if i > 0:
                content.append(" · ", style="dim")

            content.append(f"{display_name}", style="white")
            content.append(f" ({confidence_label})", style=confidence_style)
