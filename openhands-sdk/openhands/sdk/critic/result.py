import json
from typing import ClassVar

from pydantic import BaseModel, Field
from rich.text import Text


class CriticResult(BaseModel):
    """A critic result is a score and a message."""

    THRESHOLD: ClassVar[float] = 0.5

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
        content.append("\nCritic Score:\n", style="bold")

        # Display main score
        score_style = "green" if self.success else "yellow"
        content.append(f"  Overall: {self.score:.4f}\n", style=score_style)

        # Parse and display detailed probabilities if available in message
        if self.message:
            try:
                probs_dict = json.loads(self.message[self.message.find("{") :])
                if isinstance(probs_dict, dict):
                    # Sort by probability (descending)
                    sorted_probs = sorted(
                        probs_dict.items(), key=lambda x: x[1], reverse=True
                    )

                    # Display each field on a separate line with color coding
                    for field, prob in sorted_probs:
                        # Color code based on probability
                        if prob >= 0.7:
                            prob_style = "red bold"
                        elif prob >= 0.5:
                            prob_style = "red"
                        elif prob >= 0.3:
                            prob_style = "yellow"
                        elif prob >= 0.1:
                            prob_style = "white"
                        else:
                            prob_style = "dim"

                        content.append(f"  {field}: ", style="white")
                        content.append(f"{prob:.4f}\n", style=prob_style)
                else:
                    # If not a dict, display the message as-is
                    content.append(f"  {self.message}\n")
            except (json.JSONDecodeError, ValueError):
                # If JSON parsing fails, display the message as-is
                content.append(f"  {self.message}\n")

        return content
