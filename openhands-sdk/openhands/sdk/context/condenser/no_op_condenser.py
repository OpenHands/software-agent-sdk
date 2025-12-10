from typing import TYPE_CHECKING

from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation


if TYPE_CHECKING:
    from openhands.sdk.llm import LLM


class NoOpCondenser(CondenserBase):
    """Simple condenser that returns a view un-manipulated.

    Primarily intended for testing purposes.
    """

    def condense(self, view: View, llm: "LLM | None" = None) -> View | Condensation:
        return view
