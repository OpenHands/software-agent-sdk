from __future__ import annotations

from openhands.sdk.event.base import LLMConvertibleEvent


class ManipulationIndices(set[int]):
    """A set of indices where events can be safely manipulated.

    We mean two main things when we say a list of events `events` can be "manipulated":

    1. If `i` is a manipulation index, we can insert any event into `events` at `i`.
    2. If `i, j` are manipulation indices, `events[i:j]` can be deleted.

    Extends set[int] to provide utility methods for finding the next valid manipulation
    index and building common index sets.
    """

    def find_next(self, threshold: int) -> int:
        """Find the smallest manipulation index greater than or equal to the threshold.

        This is a helper method for condensation logic that needs to find safe
        boundaries for forgetting events.

        Args:
            threshold: The threshold value to compare against.

        Returns:
            The smallest manipulation index greater than or equal to the threshold.

        Raises:
            ValueError: if no valid manipulatin index exists past the threshold.
        """
        valid_indices = {idx for idx in self if idx >= threshold}

        if not valid_indices:
            raise ValueError(f"No manipulation index found >= {threshold}.")

        return min(valid_indices)

    @staticmethod
    def complete(events: list[LLMConvertibleEvent]) -> ManipulationIndices:
        """Returns a complete set of manipulation indices for a sequence of events.

        This is equivalent to saying that manipulations can be done anywhere inside the
        sequence without issue.
        """
        manipulation_indices = ManipulationIndices()

        manipulation_indices.update(range(0, len(events)))
        manipulation_indices.add(len(events))

        return manipulation_indices
