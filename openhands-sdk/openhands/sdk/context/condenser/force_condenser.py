from openhands.sdk.context.condenser.llm_summarizing_condenser import (
    LLMSummarizingCondenser,
)
from openhands.sdk.context.view import View


class ForceCondenser(LLMSummarizingCondenser):
    """A condenser that forces condensation regardless of normal window requirements.

    This class extends LLMSummarizingCondenser and overrides the should_condense method
    to always return True, bypassing the normal condensation window requirements.
    This is useful for manual condensation triggers via conversation.condense().
    """

    def should_condense(self, view: View) -> bool:
        """Always return True to force condensation regardless of view size.

        This method bypasses the normal condensation window requirements and forces
        condensation to be applied whenever requested.

        Args:
            view: A view of the history containing all events (unused)

        Returns:
            bool: Always True to force condensation
        """
        del view  # Unused parameter, but required by parent class interface
        return True
