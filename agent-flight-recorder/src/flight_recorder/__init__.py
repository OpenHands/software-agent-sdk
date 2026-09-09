"""Record and inspect OpenHands agent runs."""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from flight_recorder.recorder import Recorder


__all__ = ["Recorder"]


def __getattr__(name: str):
    if name == "Recorder":
        from flight_recorder.recorder import Recorder

        return Recorder
    raise AttributeError(name)
