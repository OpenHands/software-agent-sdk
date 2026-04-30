from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from openhands.sdk.critic.base import CriticBase, CriticResult
from openhands.sdk.critic.impl.api.client import CriticClient
from openhands.sdk.critic.impl.api.taxonomy import categorize_features
from openhands.sdk.critic.refinement import (
    DEFAULT_ISSUE_THRESHOLD,
    IterativeRefinementDecision,
)


if TYPE_CHECKING:
    from openhands.sdk.event import LLMConvertibleEvent, SystemPromptEvent


def _format_feature_list(features: list[dict[str, Any]]) -> str:
    """Format a list of features with their probabilities."""
    if not features:
        return "None detected"
    items = []
    for f in features:
        name = f.get("display_name", f.get("name", "Unknown"))
        prob = f.get("probability", 0)
        items.append(f"{name} ({prob:.0%})")
    return ", ".join(items)


def _format_feature_for_prompt(feature: dict[str, Any]) -> str:
    name = feature.get("display_name", feature.get("name", "Unknown"))
    probability = feature.get("probability", 0)
    return f"{name} ({probability:.0%})"


def _get_categorized_features(critic_result: CriticResult) -> dict[str, Any]:
    if not critic_result.metadata:
        return {}

    categorized = critic_result.metadata.get("categorized_features", {})
    if isinstance(categorized, dict):
        return categorized
    return {}


def _get_high_probability_agent_issues(
    critic_result: CriticResult,
    issue_threshold: float,
) -> tuple[dict[str, Any], ...]:
    categorized = _get_categorized_features(critic_result)
    agent_issues = categorized.get("agent_behavioral_issues", [])
    high_probability_issues = [
        issue
        for issue in agent_issues
        if isinstance(issue, dict) and issue.get("probability", 0) >= issue_threshold
    ]
    high_probability_issues.sort(
        key=lambda issue: issue.get("probability", 0), reverse=True
    )
    return tuple(high_probability_issues)


class APIBasedCritic(CriticBase, CriticClient):
    def evaluate(
        self,
        events: Sequence[LLMConvertibleEvent],
        git_patch: str | None = None,  # noqa: ARG002
    ) -> CriticResult:
        # Local imports to avoid circular dependencies during module load
        from openhands.sdk.context.view import View
        from openhands.sdk.event import LLMConvertibleEvent, SystemPromptEvent

        system_prompt_event: SystemPromptEvent | None = None
        tools = []
        for event in events:
            if isinstance(event, SystemPromptEvent):
                system_prompt_event = event
                tools = event.tools
                break
        if system_prompt_event is None:
            raise ValueError(
                "SystemPromptEvent is required for APIBasedCritic evaluation"
            )
        if not tools:
            raise ValueError(
                "APIBasedCritic requires tools to be defined in SystemPromptEvent. "
                "Ensure your agent configuration includes tool definitions."
            )

        # This will only retain events that are kept by the condenser
        view = View.from_events(events)
        llm_convertible_events = view.events

        # Convert events to messages
        messages = LLMConvertibleEvent.events_to_messages(llm_convertible_events)

        # Serialize messages to dicts for API
        formatted_messages = [
            message.to_chat_dict(
                cache_enabled=False,
                vision_enabled=False,  # Critic does not support vision currently
                function_calling_enabled=True,
                force_string_serializer=False,
                send_reasoning_content=False,
            )
            for message in messages
        ]

        # Convert ToolDefinition objects to ChatCompletionToolParam format
        tools_for_api = [tool.to_openai_tool() for tool in tools]
        response = self.classify_trace(formatted_messages, tools_for_api)
        prob_map = self.extract_prob_map(response)

        explanation = []

        if "success" not in prob_map.probs:
            raise ValueError("APIBasedCritic requires 'success' label in the response.")

        score = prob_map.probs["success"]
        explanation.append(f"Success: {score:.2f}")

        # Add top labels to explanation
        sorted_probs = sorted(prob_map.probs.items(), key=lambda x: x[1], reverse=True)
        explanation.append(json.dumps(dict(sorted_probs)))

        # Collect event IDs for reproducibility
        event_ids = [event.id for event in llm_convertible_events]

        # Categorize features for visualization
        categorized = categorize_features(prob_map.probs)

        return CriticResult(
            score=score,
            message="; ".join(explanation),
            metadata={
                "event_ids": event_ids,
                "categorized_features": categorized,
            },
        )

    def evaluate_refinement(
        self, critic_result: CriticResult | None
    ) -> IterativeRefinementDecision:
        """Use API critic taxonomy signals in addition to the score threshold."""
        decision = super().evaluate_refinement(critic_result)
        if critic_result is None:
            return decision

        issue_threshold = (
            self.iterative_refinement.issue_threshold
            if self.iterative_refinement is not None
            else DEFAULT_ISSUE_THRESHOLD
        )
        triggered_issues = _get_high_probability_agent_issues(
            critic_result,
            issue_threshold,
        )
        if triggered_issues:
            return IterativeRefinementDecision(
                should_refine=True,
                triggered_issues=triggered_issues,
            )

        return decision

    def get_followup_prompt(self, critic_result: CriticResult, iteration: int) -> str:
        """Generate a detailed follow-up prompt with rubrics predictions.

        This override provides more detailed feedback than the base class,
        including all categorized features (agent behavioral issues,
        user follow-up patterns, infrastructure issues) with their probabilities.

        Args:
            critic_result: The critic result from the previous iteration.
            iteration: The current iteration number (1-indexed).

        Returns:
            A detailed follow-up prompt string with rubrics predictions.
        """
        score_percent = critic_result.score * 100
        if self.iterative_refinement is None:
            iteration_label = f"iteration {iteration}"
            issue_threshold = DEFAULT_ISSUE_THRESHOLD
        else:
            iteration_label = (
                f"iteration {iteration}/{self.iterative_refinement.max_iterations}"
            )
            issue_threshold = self.iterative_refinement.issue_threshold

        lines = [
            f"The task appears incomplete ({iteration_label}, "
            f"predicted success likelihood: {score_percent:.1f}%).",
            "",
        ]

        categorized = _get_categorized_features(critic_result)
        triggered_issues = _get_high_probability_agent_issues(
            critic_result,
            issue_threshold,
        )
        if triggered_issues:
            lines.append("**Detected issues requiring attention:**")
            for issue in triggered_issues:
                lines.append(f"- {_format_feature_for_prompt(issue)}")
            lines.append("")

        # Extract detailed rubrics from categorized features
        if categorized:
            # Agent behavioral issues
            agent_issues = categorized.get("agent_behavioral_issues", [])
            agent_issues = [
                issue
                for issue in agent_issues
                if isinstance(issue, dict) and issue not in triggered_issues
            ]
            if agent_issues:
                lines.append(
                    f"Potential agent issues: {_format_feature_list(agent_issues)}"
                )

            # User follow-up patterns (predicted)
            user_patterns = categorized.get("user_followup_patterns", [])
            if user_patterns:
                formatted = _format_feature_list(user_patterns)
                lines.append(f"Predicted user follow-up needs: {formatted}")

            # Infrastructure issues
            infra_issues = categorized.get("infrastructure_issues", [])
            if infra_issues:
                lines.append(
                    f"Infrastructure issues: {_format_feature_list(infra_issues)}"
                )

            # Other metrics
            other = categorized.get("other", [])
            if other:
                lines.append(f"Other observations: {_format_feature_list(other)}")

            if agent_issues or user_patterns or infra_issues or other:
                lines.append("")

        lines.extend(
            [
                "Please review what you've done and verify each requirement is met.",
                "List what's working and what needs fixing, then complete the task.",
            ]
        )

        return "\n".join(lines)
