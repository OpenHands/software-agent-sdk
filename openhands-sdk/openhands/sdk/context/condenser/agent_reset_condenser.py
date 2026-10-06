from openhands.sdk.context.condenser.base import (
    CondenserBase,
    NoCondensationAvailableException,
)
from openhands.sdk.context.condenser.llm_summarizing_condenser import (
    LLMSummarizingCondenser,
)
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.context.view import View
from openhands.sdk.event import (
    ActionEvent,
    Condensation,
    ContextWindowReminderEvent,
    LLMConvertibleEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import LLM
from openhands.sdk.tool.builtins.new_context import NewContextObservation


class AgentResetCondenser(CondenserBase):
    """Let the agent reset its context, with a bounded summary fallback."""

    def required_tools(self) -> frozenset[str]:
        return frozenset({"new_context", "conversation_history"})

    def handles_condensation_requests(self) -> bool:
        return True

    def get_reminder(
        self,
        view: View,
        agent_llm: LLM | None = None,
        *,
        token_count: int | None = None,
    ) -> ContextWindowReminderEvent | None:
        if view.context_window_reminded or agent_llm is None:
            return None
        capacity = agent_llm.effective_max_input_tokens
        if capacity is None:
            return None
        if token_count is None:
            token_count = get_total_token_count(view.events, agent_llm)
        if capacity * 0.8 <= token_count < capacity:
            return ContextWindowReminderEvent()
        return None

    def is_over_capacity(
        self,
        view: View,
        agent_llm: LLM,
        *,
        token_count: int | None = None,
    ) -> bool:
        capacity = agent_llm.effective_max_input_tokens
        if capacity is None:
            return False
        if token_count is None:
            token_count = get_total_token_count(view.events, agent_llm)
        return token_count >= capacity

    def condense(self, view: View, agent_llm: LLM | None = None) -> View | Condensation:
        request = view.pending_condensation_request
        if request is None:
            return view
        if request.trigger_action_id is None:
            return self.hard_context_reset(view, agent_llm)
        result = self._reset_from_action(view, request.trigger_action_id)
        if (
            result.forgotten_event_ids
            and agent_llm is not None
            and self.is_over_capacity(View(events=result.apply(view.events)), agent_llm)
        ):
            raise NoCondensationAvailableException(
                "The context reset cannot fit its protected input and tool batch."
            )
        return result

    async def acondense(
        self, view: View, agent_llm: LLM | None = None
    ) -> View | Condensation:
        request = view.pending_condensation_request
        if request is not None and request.trigger_action_id is None:
            return await self.ahard_context_reset(view, agent_llm)
        return self.condense(view, agent_llm)

    def _reset_from_action(self, view: View, action_id: str) -> Condensation:
        trigger = next(
            (
                event
                for event in view.events
                if isinstance(event, ActionEvent)
                and event.id == action_id
                and event.tool_name == "new_context"
            ),
            None,
        )
        observation = next(
            (
                event.observation
                for event in view.events
                if isinstance(event, ObservationEvent)
                and event.action_id == action_id
                and isinstance(event.observation, NewContextObservation)
                and not event.observation.is_error
            ),
            None,
        )
        if trigger is None or observation is None:
            raise NoCondensationAvailableException(
                "Context reset requires a completed, successful new_context action."
            )

        positions = {event.id: index for index, event in enumerate(view.events)}
        boundary = positions.get(observation.input_event_id or "")
        if boundary is not None and boundary >= positions[trigger.id]:
            boundary = None
        users = self._user_indices(view)
        protected = self._system_indices(view)
        protected.update(
            index for index in users if boundary is None or index > boundary
        )
        protected.update(
            index
            for index, event in enumerate(view.events)
            if isinstance(event, ActionEvent)
            and event.llm_response_id == trigger.llm_response_id
        )
        forgotten = self._removable_events(view, protected)
        return Condensation(
            forgotten_event_ids={event.id for event in forgotten},
            llm_response_id=trigger.llm_response_id,
        )

    @staticmethod
    def _user_indices(view: View) -> set[int]:
        return {
            index
            for index, event in enumerate(view.events)
            if isinstance(event, MessageEvent) and event.source == "user"
        }

    @staticmethod
    def _system_indices(view: View) -> set[int]:
        return {
            index
            for index, event in enumerate(view.events)
            if isinstance(event, SystemPromptEvent)
        }

    @staticmethod
    def _removable_events(view: View, protected: set[int]) -> list[LLMConvertibleEvent]:
        boundaries = sorted(view.manipulation_indices)
        return [
            event
            for start, end in zip(boundaries, boundaries[1:])
            if not protected.intersection(range(start, end))
            for event in view.events[start:end]
        ]

    def _summary_view(self, view: View) -> View:
        forgotten = self._removable_events(
            view, self._system_indices(view) | self._user_indices(view)
        )
        if not forgotten:
            raise NoCondensationAvailableException(
                "No history can be summarized while preserving system and user input."
            )
        return View(events=forgotten)

    def _validate_summary(
        self, view: View, result: Condensation | None, agent_llm: LLM
    ) -> Condensation:
        if result is None or not result.summary:
            raise NoCondensationAvailableException("Context summary generation failed.")
        first_removed = next(
            index
            for index, event in enumerate(view.events)
            if event.id in result.forgotten_event_ids
        )
        result = result.model_copy(update={"summary_offset": first_removed})
        retained = View(events=result.apply(view.events))
        if get_total_token_count(retained.events, agent_llm) >= get_total_token_count(
            view.events, agent_llm
        ):
            raise NoCondensationAvailableException(
                "Context summary did not reduce the model input."
            )
        if self.is_over_capacity(retained, agent_llm):
            raise NoCondensationAvailableException(
                "Protected input and the summary still exceed the context capacity."
            )
        return result

    def hard_context_reset(
        self, view: View, agent_llm: LLM | None = None
    ) -> Condensation:
        if agent_llm is None:
            raise NoCondensationAvailableException("Context summary requires an LLM.")
        summary_view = self._summary_view(view)
        result = LLMSummarizingCondenser(llm=agent_llm).hard_context_reset(summary_view)
        return self._validate_summary(view, result, agent_llm)

    async def ahard_context_reset(
        self, view: View, agent_llm: LLM | None = None
    ) -> Condensation:
        if agent_llm is None:
            raise NoCondensationAvailableException("Context summary requires an LLM.")
        summary_view = self._summary_view(view)
        result = await LLMSummarizingCondenser(llm=agent_llm).ahard_context_reset(
            summary_view
        )
        return self._validate_summary(view, result, agent_llm)
