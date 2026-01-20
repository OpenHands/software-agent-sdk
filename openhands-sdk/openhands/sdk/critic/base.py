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

    @abc.abstractmethod
    def evaluate(
        self, events: Sequence["LLMConvertibleEvent"], git_patch: str | None = None
    ) -> CriticResult:
        pass
