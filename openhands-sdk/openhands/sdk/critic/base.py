import abc
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from openhands.sdk.critic.result import CriticResult
from openhands.sdk.utils.models import DiscriminatedUnionMixin


if TYPE_CHECKING:
    from openhands.sdk.event.base import LLMConvertibleEvent


class CriticBase(DiscriminatedUnionMixin, abc.ABC):
    """A critic is a function that takes in a list of events,
    optional git patch, and returns a score about the quality of agent's action.
    """

    mode: Literal["finish_and_message", "all_actions"] = Field(
        default="finish_and_message",
        description=(
            "When to run critic evaluation:\n"
            "- 'finish_and_message': Evaluate on FinishAction and agent"
            " MessageEvent (default, minimal performance impact)\n"
            "- 'all_actions': Evaluate after every agent action (WARNING: "
            "significantly slower due to API calls on each action)"
        ),
    )

    @abc.abstractmethod
    def evaluate(
        self, events: Sequence["LLMConvertibleEvent"], git_patch: str | None = None
    ) -> CriticResult:
        pass
