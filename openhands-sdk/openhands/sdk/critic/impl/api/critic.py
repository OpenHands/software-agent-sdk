from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from openhands.sdk.critic.base import CriticBase, CriticResult
from openhands.sdk.critic.impl.api.client import CriticClient
from openhands.sdk.critic.impl.api.taxonomy import categorize_features
from openhands.sdk.llm.serialization_context import LLMSerializationContext


if TYPE_CHECKING:
    from openhands.sdk.event import LLMConvertibleEvent, SystemPromptEvent


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
            raise ValueError("Tools are required for APIBasedCritic evaluation")

        # This will only retain events that are kept by the condenser
        view = View.from_events(events)
        llm_convertible_events = view.events

        # Convert events to messages
        messages = LLMConvertibleEvent.events_to_messages(llm_convertible_events)

        # Serialize messages to dicts for API
        formatted_messages = [
            message.to_chat_dict(
                ctx=LLMSerializationContext(
                    cache_enabled=False,
                    vision_enabled=False,  # Critic does not support vision currently
                    function_calling_enabled=True,
                    force_string_serializer=False,
                    send_reasoning_content=False,
                )
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
