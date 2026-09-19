import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCallIdPolicy:
    """Constraints for rendering a tool-call ID on a provider wire protocol."""

    allowed_pattern: re.Pattern[str]
    generated_prefix: str = "id_"
    max_length: int | None = None

    def encode(self, value: str) -> str:
        """Preserve accepted IDs and deterministically encode rejected IDs."""
        if self._accepts(value):
            return value

        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        if self.max_length is not None:
            digest = digest[: self.max_length - len(self.generated_prefix)]

        encoded = f"{self.generated_prefix}{digest}"
        if not self._accepts(encoded):
            raise ValueError("Tool-call ID policy rejects its generated IDs")
        return encoded

    def _accepts(self, value: str) -> bool:
        if self.max_length is not None and len(value) > self.max_length:
            return False
        return self.allowed_pattern.fullmatch(value) is not None


OPENAI_RESPONSES_TOOL_CALL_ID_POLICY = ToolCallIdPolicy(
    allowed_pattern=re.compile(r"^[A-Za-z0-9_-]+$"),
)
