import json
from collections.abc import Sequence

from openhands.sdk.context.view import View
from openhands.sdk.critic.base import CriticBase, CriticResult
from openhands.sdk.critic.impl.api.client import CriticClient
from openhands.sdk.event import LLMConvertibleEvent, SystemPromptEvent


class APIBasedCritic(CriticBase, CriticClient):
    def evaluate(
        self,
        events: Sequence[LLMConvertibleEvent],
        git_patch: str | None = None,  # noqa: ARG002
    ) -> CriticResult:
        system_prompt_event = None
        tools = []
        for event in events:
            if isinstance(event, SystemPromptEvent):
                system_prompt_event = event
                tools = event.tools
                break
        assert system_prompt_event is not None, (
            "SystemPromptEvent is required for APIBasedCritic"
        )
        assert tools is not None, "Tools are required for APIBasedCritic"

        # This will only retain events that are kept by the condenser
        view = View.from_events(events)
        llm_convertible_events = view.events

        # Convert events to messages
        messages = LLMConvertibleEvent.events_to_messages(llm_convertible_events)

        # Serialize messages to dicts for API
        for message in messages:
            message.cache_enabled = False
            message.vision_enabled = False  # Critic does not support vision currently
            message.function_calling_enabled = True
            message.force_string_serializer = False
            message.send_reasoning_content = False
        formatted_messages = [message.to_chat_dict() for message in messages]

        response = self.classify_trace(formatted_messages, tools)
        prob_map = self.extract_prob_map(response)

        score = 0.0
        explanation = []

        if "success" not in prob_map.probs:
            raise ValueError("APIBasedCritic requires 'success' label in the response.")

        score = prob_map.probs["success"]
        explanation.append(f"Success: {score:.2f}")

        # Add top labels to explanation
        sorted_probs = sorted(prob_map.probs.items(), key=lambda x: x[1], reverse=True)
        explanation.append(json.dumps(dict(sorted_probs)))

        return CriticResult(score=score, message="; ".join(explanation))
