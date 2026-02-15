"""Insight tool for session analysis and personalization.

This tool provides session analysis capabilities by scanning historical
conversation data and generating usage reports with optimization suggestions.
"""

from openhands.tools.insight.definition import (
    InsightAction,
    InsightObservation,
    InsightTool,
)


__all__ = [
    "InsightTool",
    "InsightAction",
    "InsightObservation",
]
