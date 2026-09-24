from pydantic import Field

from openhands.sdk.context.condenser.base import (
    CondensationRequirement,
    NoCondensationAvailableException,
    RollingCondenser,
)
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.context.view import View
from openhands.sdk.event import MessageEvent, ObservationEvent, SystemPromptEvent
from openhands.sdk.event.condenser import ContextWindowReminderEvent, HistoryIndexEvent
from openhands.sdk.llm import LLM
from openhands.sdk.tool.builtins.context_notes import ContextNotesObservation


class NotesRetrievalCondenser(RollingCondenser):
    """Keep a compact history index and retrieve original events on demand."""

    max_size: int = Field(default=240, ge=4)
    max_tokens: int | None = Field(default=None, gt=0)
    keep_first: int = Field(default=4, ge=0)
    keep_recent: int = Field(default=8, ge=0)
    reminder_fraction: float = Field(default=0.8, gt=0, lt=1)

    def required_tools(self) -> frozenset[str]:
        return frozenset({"conversation_history", "context_notes"})

    def handles_condensation_requests(self) -> bool:
        return True

    def _token_limit(self, agent_llm: LLM | None) -> int | None:
        limits = [
            limit
            for limit in (
                self.max_tokens,
                agent_llm.effective_max_input_tokens if agent_llm is not None else None,
            )
            if limit is not None
        ]
        return min(limits) if limits else None

    def _at_threshold(
        self, view: View, agent_llm: LLM | None, fraction: float = 1.0
    ) -> bool:
        if len(view) >= self.max_size * fraction:
            return True
        token_limit = self._token_limit(agent_llm)
        return (
            agent_llm is not None
            and token_limit is not None
            and get_total_token_count(view.events, agent_llm) >= token_limit * fraction
        )

    def get_reminder(
        self, view: View, agent_llm: LLM | None = None
    ) -> ContextWindowReminderEvent | None:
        if (
            view.latest_condensation_id in view.reminded_window_ids
            or view.unhandled_condensation_request
            or self._at_threshold(view, agent_llm)
            or not self._at_threshold(view, agent_llm, self.reminder_fraction)
        ):
            return None
        return ContextWindowReminderEvent(window_id=view.latest_condensation_id)

    def condensation_requirement(
        self, view: View, agent_llm: LLM | None = None
    ) -> CondensationRequirement | None:
        if view.unhandled_condensation_request or self._at_threshold(view, agent_llm):
            return CondensationRequirement.HARD
        return None

    def get_condensation(
        self, view: View, agent_llm: LLM | None = None
    ) -> HistoryIndexEvent:
        protected = set(range(min(self.keep_first, len(view))))
        protected.update(range(max(0, len(view) - self.keep_recent), len(view)))
        for i, event in enumerate(view.events):
            if isinstance(event, SystemPromptEvent):
                protected.add(i)
        latest_user = next(
            (
                i
                for i in range(len(view) - 1, -1, -1)
                if isinstance(view.events[i], MessageEvent)
                and view.events[i].source == "user"
            ),
            None,
        )
        if latest_user is not None:
            protected.add(latest_user)

        boundaries = sorted(view.manipulation_indices)
        forgotten_indices = [
            i
            for start, end in zip(boundaries, boundaries[1:])
            if not any(i in protected for i in range(start, end))
            for i in range(start, end)
        ]
        if len(forgotten_indices) < 2:
            raise NoCondensationAvailableException(
                "Cannot reduce context while preserving pinned events and complete "
                "tool exchanges. Reduce keep_first/keep_recent or the current input."
            )

        notes_event_id = None
        for event in view.events:
            if isinstance(event, HistoryIndexEvent):
                notes_event_id = event.notes_event_id
            elif (
                isinstance(event, ObservationEvent)
                and isinstance(event.observation, ContextNotesObservation)
                and event.observation.command in ("write", "append")
                and not event.observation.is_error
            ):
                notes_event_id = event.id

        index = HistoryIndexEvent(
            forgotten_event_ids={view.events[i].id for i in forgotten_indices},
            index_offset=forgotten_indices[0],
            previous_window_id=view.latest_condensation_id,
            notes_event_id=notes_event_id,
            first_forgotten_event_id=view.events[forgotten_indices[0]].id,
            last_forgotten_event_id=view.events[forgotten_indices[-1]].id,
        )
        reduced = index.apply(view.events)
        token_limit = self._token_limit(agent_llm)
        if len(reduced) >= self.max_size or (
            agent_llm is not None
            and token_limit is not None
            and (
                get_total_token_count(reduced, agent_llm) >= token_limit
                or get_total_token_count(reduced, agent_llm)
                >= get_total_token_count(view.events, agent_llm)
            )
        ):
            raise NoCondensationAvailableException(
                "The retained context and history index exceed the context budget. "
                "Reduce the current input or the pinned/recent event limits."
            )
        return index
