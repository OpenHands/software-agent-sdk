#!/usr/bin/env python3

"""
Utility function to format cost values with appropriate precision.
"""


def format_cost(value: float) -> str:
    """
    Format cost with smart precision to show meaningful values even for small amounts.

    Args:
        value: The cost value to format (must be a numeric value)

    Returns:
        Formatted cost string with appropriate precision
    """
    if value == 0.0:
        # Handle zero as a special case
        return "$0.00"
    if abs(value) >= 0.01:
        # Normal rounding for typical amounts
        return f"${value:.2f}"
    if abs(value) >= 0.001:
        # Round small numbers to 2 significant figures
        return f"${value:.2g}"
    # Use scientific notation for very small numbers
    return f"${value:.1e}"
