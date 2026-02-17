class ManipulationIndices(set[int]):
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
