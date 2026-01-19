import json
from typing import ClassVar

from pydantic import BaseModel, Field
from rich.text import Text


class CriticResult(BaseModel):
    """A critic result is a score and a message."""

    THRESHOLD: ClassVar[float] = 0.5
    DISPLAY_THRESHOLD: ClassVar[float] = 0.1  # Only show scores above this threshold

    score: float = Field(
        description="A predicted probability of success between 0 and 1.",
        ge=0.0,
        le=1.0,
    )
    message: str | None = Field(description="An optional message explaining the score.")

    @property
    def success(self) -> bool:
        """Whether the agent is successful."""
        return self.score >= CriticResult.THRESHOLD

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of the critic result."""
        content = Text()
        content.append("\nCritic Score: ", style="bold")

        # Display main score inline
        score_style = "green" if self.success else "yellow"
        content.append(f"{self.score:.4f}", style=score_style)

        # Parse and display detailed probabilities if available in message
        if self.message:
            try:
                probs_dict = json.loads(self.message[self.message.find("{") :])
                if isinstance(probs_dict, dict):
                    # Separate sentiments from other metrics
                    sentiments = {}
                    other_metrics = {}

                    for field, prob in probs_dict.items():
                        if field.startswith("sentiment_"):
                            sentiments[field] = prob
                        else:
                            other_metrics[field] = prob

                    # Normalize and display sentiments if present
                    if sentiments:
                        sentiment_sum = sum(sentiments.values())
                        if sentiment_sum > 0:
                            content.append(" | ", style="dim")
                            content.append("Sentiment: ", style="bold")

                            # Sort sentiments by probability
                            sorted_sentiments = sorted(
                                sentiments.items(), key=lambda x: x[1], reverse=True
                            )

                            for i, (field, prob) in enumerate(sorted_sentiments):
                                # Normalize to percentage
                                normalized = (prob / sentiment_sum) * 100

                                # Shorten names: sentiment_neutral -> neutral
                                short_name = field.replace("sentiment_", "")

                                # Color code based on normalized percentage
                                if normalized >= 60:
                                    style = "cyan bold"
                                elif normalized >= 30:
                                    style = "cyan"
                                else:
                                    style = "dim"

                                if i > 0:
                                    content.append(", ", style="dim")
                                content.append(f"{short_name} ", style="white")
                                content.append(f"{normalized:.1f}%", style=style)

                    # Filter and display other significant metrics
                    significant_metrics = {
                        k: v
                        for k, v in other_metrics.items()
                        if v >= self.DISPLAY_THRESHOLD
                    }

                    if significant_metrics:
                        # Sort by probability (descending)
                        sorted_metrics = sorted(
                            significant_metrics.items(),
                            key=lambda x: x[1],
                            reverse=True,
                        )

                        content.append("\n  ", style="dim")

                        # Display metrics in a compact format (multiple per line)
                        for i, (field, prob) in enumerate(sorted_metrics):
                            # Color code based on probability
                            if prob >= 0.7:
                                prob_style = "red bold"
                            elif prob >= 0.5:
                                prob_style = "red"
                            elif prob >= 0.3:
                                prob_style = "yellow"
                            else:
                                prob_style = "white"

                            if i > 0:
                                content.append(" • ", style="dim")
                            content.append(f"{field}: ", style="dim")
                            content.append(f"{prob:.2f}", style=prob_style)

                        content.append("\n")
                    else:
                        content.append("\n")
                else:
                    # If not a dict, display the message as-is
                    content.append(f"\n  {self.message}\n")
            except (json.JSONDecodeError, ValueError):
                # If JSON parsing fails, display the message as-is
                content.append(f"\n  {self.message}\n")
        else:
            content.append("\n")

        return content
