from flight_recorder.models.envelopes import TokenUsage
from PySide6.QtWidgets import QLabel


class MetricsWidget(QLabel):
    def set_metrics(self, usage: TokenUsage, cost: float) -> None:
        self.setText(
            f"Input {usage.prompt_tokens}  Output {usage.completion_tokens}  "
            f"Cost ${cost:.4f}"
        )
