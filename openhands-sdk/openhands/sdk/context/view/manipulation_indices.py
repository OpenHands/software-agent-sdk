class ManipulationIndices(set[int]):
    """A set of indices where events can be safely manipulated.

    This class extends set[int] to provide utility methods for finding
    the next valid manipulation index given a threshold.
    """

    def find_next(self, threshold: int, strict: bool = False) -> int:
        """Find the smallest manipulation index greater than (or equal to) a threshold.

        This is a helper method for condensation logic that needs to find safe
        boundaries for forgetting events.

        Args:
            threshold: The threshold value to compare against
            strict: If True, finds index > threshold. If False, finds index >= threshold

        Returns:
            The smallest manipulation index that satisfies the condition

        Raises:
            ValueError: If no valid manipulation index exists that satisfies
                the condition
        """
        if strict:
            valid_indices = [idx for idx in self if idx > threshold]
        else:
            valid_indices = [idx for idx in self if idx >= threshold]

        if not valid_indices:
            operator = ">" if strict else ">="
            raise ValueError(
                f"No manipulation index found {operator} {threshold}. "
                f"Available indices: {sorted(self)}"
            )

        return min(valid_indices)
