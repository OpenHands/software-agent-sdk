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

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of the critic result."""
        content = Text()
        content.append("\n\nCritic Score: ", style="bold")

        # Display main score inline
        score_style = "green" if self.success else "yellow"
        content.append(f"{self.score:.4f}", style=score_style)

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
        content.append("Predicted User Sentiment: ", style="bold")

        predicted = sentiment.get("predicted", "")
        prob = sentiment.get("probability", 0.0)

        # Color sentiment based on type
        if predicted == "Positive":
            sentiment_style = "green"
        elif predicted == "Negative":
            sentiment_style = "red"
        else:  # Neutral
            sentiment_style = "yellow"

        content.append(f"{predicted} ({prob:.2f})", style=sentiment_style)

    def _append_categorized_features(
        self, content: Text, categorized: dict[str, Any]
    ) -> None:
        """Append categorized features to content, each category on its own line."""
        has_content = False

        # Agent behavioral issues
        agent_issues = categorized.get("agent_behavioral_issues", [])
        if agent_issues:
            content.append("\n")
            content.append("Agent Issues: ", style="bold yellow")
            self._append_feature_list_inline(content, agent_issues)
            has_content = True

        # User follow-up patterns
        user_patterns = categorized.get("user_followup_patterns", [])
        if user_patterns:
            content.append("\n")
            content.append("User Follow-Up: ", style="bold yellow")
            self._append_feature_list_inline(content, user_patterns)
            has_content = True

        # Infrastructure issues
        infra_issues = categorized.get("infrastructure_issues", [])
        if infra_issues:
            content.append("\n")
            content.append("Infra Issues: ", style="bold yellow")
            self._append_feature_list_inline(content, infra_issues)
            has_content = True

        # Other metrics
        other = categorized.get("other", [])
        if other:
            content.append("\n")
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
        """Append features inline with dot separators."""
        for i, feature in enumerate(features):
            display_name = feature.get("display_name", feature.get("name", "Unknown"))
            prob = feature.get("probability", 0.0)

            # Determine color based on probability
            if is_other:
                prob_style = "white"
            elif prob >= 0.7:
                prob_style = "red bold"
            elif prob >= 0.5:
                prob_style = "red"
            elif prob >= 0.3:
                prob_style = "yellow"
            else:
                prob_style = "dim"

            # Add dot separator between features
            if i > 0:
                content.append(" · ", style="dim")

            content.append(f"{display_name}: ", style="dim")
            content.append(f"{prob:.2f}", style=prob_style)
