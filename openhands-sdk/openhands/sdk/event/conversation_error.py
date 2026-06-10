import re

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event


# ---------------------------------------------------------------------------
# Hint rules: list of (pattern, hint_text) pairs.  The first matching pattern
# wins.  Patterns are matched case-insensitively against ``detail``.
# ---------------------------------------------------------------------------
_HINT_RULES: list[tuple[re.Pattern[str], str]] = [
    # Databricks AI Gateway: endpoint not found in workspace (404).
    # Typically means cross-geography routing is disabled, or the endpoint
    # has not been deployed in this workspace.
    (
        re.compile(
            r"\[404\]\s*AI\s+Gateway\s+endpoint\s+['\"]?(\S+?)['\"]?\s+does\s+not\s+exist",
            re.IGNORECASE,
        ),
        (
            "This Databricks endpoint is not available in your workspace.\n"
            "Possible reasons:\n"
            "  • The model requires cross-geography routing, which is not\n"
            "    enabled in your workspace (contact your admin).\n"
            "  • The endpoint name is misspelled or not yet deployed.\n"
            "Tip: Open Settings → click 'Refresh Models' to see the endpoints\n"
            "that are actually available in your workspace, then save a\n"
            "different model."
        ),
    ),
    # Databricks: org-level access denied (403 Invalid access to Org).
    # Gemini and other cross-geography models route through a Databricks
    # global GCP org.  The 403 means that routing is not enabled for this
    # workspace account.
    (
        re.compile(r"\[403\].*Invalid\s+access\s+to\s+Org", re.IGNORECASE),
        (
            "Your workspace does not have permission to access this model.\n"
            "This error most commonly occurs with Gemini models, which require\n"
            "cross-geography routing through a Databricks GCP organisation.\n"
            "Action: ask your Databricks account admin to enable\n"
            "  'Cross-geography model serving' for your account, or choose a\n"
            "  different model (Claude / Llama / DBRX) that runs within your\n"
            "  workspace region.\n"
            "Tip: Open Settings → click '↻ Refresh Models' to see only the\n"
            "endpoints available in your workspace, then pick a different model."
        ),
    ),
    # Databricks: authentication failure (401 / token expired).
    (
        re.compile(r"\[401\].*databricks|databricks.*\[401\]|UNAUTHENTICATED", re.IGNORECASE),
        (
            "Databricks authentication failed.\n"
            "Tip: Open Settings and re-authenticate (re-run the browser sign-in\n"
            "for U2M, or verify your client credentials for M2M)."
        ),
    ),
    # Generic LiteLLM / provider rate-limit.
    (
        re.compile(r"\[429\]|rate.?limit|too many requests", re.IGNORECASE),
        "The model endpoint returned a rate-limit error.  Wait a moment and retry.",
    ),
]


def _get_hint(detail: str) -> str | None:
    """Return the first matching hint for the given error detail, or None."""
    for pattern, hint in _HINT_RULES:
        if pattern.search(detail):
            return hint
    return None


class ConversationErrorEvent(Event):
    """
    Conversation-level failure that is NOT sent back to the LLM.

    This event is emitted by the conversation runtime when an unexpected
    exception bubbles up and prevents the run loop from continuing. It is
    intended for client applications (e.g., UIs) to present a top-level error
    state, and for orchestration to react. It is not an observation and it is
    not LLM-convertible.

    Differences from AgentErrorEvent:
    - Not tied to any tool_name/tool_call_id (AgentErrorEvent is a tool
      observation).
    - Typically source='environment' and the run loop moves to an ERROR state,
      while AgentErrorEvent has source='agent' and the conversation can
      continue.
    """

    code: str = Field(description="Code for the error - typically a type")
    detail: str = Field(description="Details about the error")

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this conversation error event."""
        content = Text()
        content.append("Conversation Error\n", style="bold")
        content.append("Code: ", style="bold")
        content.append(self.code)
        content.append("\n\nDetail:\n", style="bold")
        content.append(self.detail)

        hint = _get_hint(self.detail)
        if hint:
            content.append("\n\nHint:\n", style="bold yellow")
            content.append(hint, style="yellow")

        return content
