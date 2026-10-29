import json
from collections import Counter

from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.types import StuckDetectionThresholds
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    CondensationSummaryEvent,
    Event,
    MessageEvent,
    ObservationBaseEvent,
    ObservationEvent,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


# Maximum recent events to scan for stuck detection.
# This window should be large enough to capture repetitive patterns
# (4 repeats × 2 events per cycle = 8 events minimum, plus buffer for user messages)
MAX_EVENTS_TO_SCAN_FOR_STUCK_DETECTION: int = 20


class StuckDetector:
    """Detects when an agent is stuck in repetitive or unproductive patterns.

    This detector analyzes the conversation history to identify various stuck patterns:
    1. Repeating action-observation cycles
    2. Repeating action-error cycles
    3. Agent monologue (repeated messages without user input)
    4. Repeating alternating action-observation patterns
    5. Context window errors indicating memory issues
    """

    state: ConversationState
    thresholds: StuckDetectionThresholds

    def __init__(
        self,
        state: ConversationState,
        thresholds: StuckDetectionThresholds | None = None,
    ):
        self.state = state
        self.thresholds = thresholds or StuckDetectionThresholds()
        # Id of the AgentErrorEvent already nudged for, so a frozen streak
        # (e.g. an empty/reasoning-only response that adds no new action)
        # doesn't re-emit the same nudge every iteration.
        self._last_nudged_error_event_id: str | None = None

    @property
    def action_observation_threshold(self) -> int:
        return self.thresholds.action_observation

    @property
    def action_error_threshold(self) -> int:
        return self.thresholds.action_error

    @property
    def monologue_threshold(self) -> int:
        return self.thresholds.monologue

    @property
    def alternating_pattern_threshold(self) -> int:
        return self.thresholds.alternating_pattern

    def _events_since_last_user_message(
        self, limit: int = MAX_EVENTS_TO_SCAN_FOR_STUCK_DETECTION
    ) -> list[Event]:
        """Events in the scan window, after the last user message (if any).

        Windowed rather than full-history to avoid materializing large
        file-backed event logs.
        """
        events = self.state.active_branch(limit=limit)

        last_user_msg_index = next(
            (
                i
                for i in reversed(range(len(events)))
                if isinstance(events[i], MessageEvent) and events[i].source == "user"
            ),
            -1,  # Default to -1 if no user message found
        )
        if last_user_msg_index != -1:
            events = events[last_user_msg_index + 1 :]
        return events

    def _collect_actions_and_observations(
        self, events: list[Event], max_needed: int
    ) -> tuple[list[Event], list[Event]]:
        """The last ``max_needed`` actions and observations, most recent first."""
        last_actions: list[Event] = []
        last_observations: list[Event] = []
        for event in reversed(events):
            if isinstance(event, ActionEvent) and len(last_actions) < max_needed:
                last_actions.append(event)
            elif (
                isinstance(event, ObservationBaseEvent)
                and len(last_observations) < max_needed
            ):
                last_observations.append(event)
            if len(last_actions) >= max_needed and len(last_observations) >= max_needed:
                break
        return last_actions, last_observations

    def is_stuck(self) -> bool:
        """Check if the agent is currently stuck."""
        events = self._events_since_last_user_message()

        # Determine minimum events needed
        min_threshold = min(
            self.action_observation_threshold,
            self.action_error_threshold,
            self.monologue_threshold,
        )
        if len(events) < min_threshold:
            return False

        logger.debug(f"Checking for stuck patterns in {len(events)} events")
        logger.debug(
            f"Events after last user message: {[type(e).__name__ for e in events]}"
        )

        # action_error uses its own window (see _action_error_pairs)
        last_actions, last_observations = self._collect_actions_and_observations(
            events, self.action_observation_threshold
        )

        # Check all stuck patterns
        # scenario 1: same action, same observation
        if self._is_stuck_repeating_action_observation(last_actions, last_observations):
            return True

        # scenario 2: same action, errors
        if self._is_stuck_repeating_action_error(*self._action_error_pairs(events)):
            return True

        # scenario 3: monologue
        if self._is_stuck_monologue(events):
            return True

        # scenario 4: action, observation alternating pattern
        if len(events) >= self.alternating_pattern_threshold:
            if self._is_stuck_alternating_action_observation(events):
                return True

        # scenario 5: context window error loop
        if len(events) >= 10:
            if self._is_stuck_context_window_error(events):
                return True

        return False

    def _is_stuck_repeating_action_observation(
        self, last_actions: list[Event], last_observations: list[Event]
    ) -> bool:
        # scenario 1: same action, same observation
        threshold = self.action_observation_threshold
        if self._invalid_calls_share_response(last_actions[:threshold]):
            return False

        # Check for a loop of identical action-observation pairs
        if len(last_actions) >= threshold and len(last_observations) >= threshold:
            logger.debug(
                f"Found {len(last_actions)} actions and "
                f"{len(last_observations)} observations, checking for equality"
            )
            actions_equal = all(
                self._event_eq(last_actions[0], action)
                for action in last_actions[:threshold]
            )
            observations_equal = all(
                self._event_eq(last_observations[0], observation)
                for observation in last_observations[:threshold]
            )
            logger.debug(
                f"Actions equal: {actions_equal}, "
                f"Observations equal: {observations_equal}"
            )

            if actions_equal and observations_equal:
                logger.warning("Action, Observation loop detected")
                return True
        else:
            logger.debug(
                f"Not enough actions/observations: {len(last_actions)} actions,"
                f" {len(last_observations)} observations"
            )

        return False

    def _action_error_pairs(
        self, events: list[Event] | None = None
    ) -> tuple[list[Event], list[Event]]:
        """Actions and observations covering a full action-error streak.

        The streak counts responses, and each parallel call adds an action and
        an error, so widen the window by what the widest response adds over
        the threshold plus the extra pair that follows a nudge. ``events`` is
        the default window, when already loaded.
        """
        limit = MAX_EVENTS_TO_SCAN_FOR_STUCK_DETECTION
        while True:
            if events is None:
                events = self._events_since_last_user_message(limit)
            last_actions, last_observations = self._collect_actions_and_observations(
                events, len(events)
            )
            widths = Counter(
                action.llm_response_id
                for action in last_actions
                if isinstance(action, ActionEvent)
            )
            needed = MAX_EVENTS_TO_SCAN_FOR_STUCK_DETECTION + 2 * (
                max(widths.values(), default=1) - 1
            ) * (self.action_error_threshold + 1)
            if needed <= limit:
                return last_actions, last_observations
            limit = needed
            events = None

    def _action_error_streak(
        self, last_actions: list[Event], last_observations: list[Event]
    ) -> int:
        """Number of trailing responses that repeat one failing action."""
        if not last_actions or not last_observations:
            return 0
        reference = last_actions[0]
        if not isinstance(reference, ActionEvent):
            return 0
        remaining = list(last_observations)
        reference_error: AgentErrorEvent | None = None
        streak = 0
        counted_response: str | None = None
        for action in last_actions:
            if not isinstance(action, ActionEvent):
                break
            index = next(
                (
                    i
                    for i, obs in enumerate(remaining)
                    if isinstance(obs, ObservationBaseEvent)
                    and obs.tool_call_id == action.tool_call_id
                ),
                None,
            )
            if index is None:
                break
            observation = remaining.pop(index)
            if not isinstance(observation, AgentErrorEvent):
                break
            if reference_error is None:
                reference_error = observation
            if not self._same_error_action(
                reference, action, reference_error, observation
            ):
                break
            # Parallel calls are one attempt: the model sees their errors together.
            if action.llm_response_id != counted_response:
                counted_response = action.llm_response_id
                streak += 1
        return streak

    def _same_error_action(
        self,
        reference: ActionEvent,
        action: ActionEvent,
        reference_error: AgentErrorEvent,
        error: AgentErrorEvent,
    ) -> bool:
        if reference.tool_name != action.tool_name:
            return False
        if reference.action is None and action.action is None:
            left = reference_error.validation_errors
            right = error.validation_errors
            if left and right:
                # Custom validators can report different repairs at one location.
                if not any(
                    e.type in ("value_error", "assertion_error")
                    for e in [*left, *right]
                ):
                    return Counter(
                        (e.type, tuple(0 if isinstance(p, int) else p for p in e.loc))
                        for e in left
                    ) == Counter(
                        (e.type, tuple(0 if isinstance(p, int) else p for p in e.loc))
                        for e in right
                    )
        return self._event_eq(reference, action)

    def _invalid_calls_share_response(self, actions: list[Event]) -> bool:
        """Whether invalid calls being compared include parallel calls.

        Invalid calls are compared without their thought, so parallel calls
        can look like a loop before the model has seen any of their errors.
        """
        response_ids = [
            action.llm_response_id
            for action in actions
            if isinstance(action, ActionEvent) and action.action is None
        ]
        return len(response_ids) != len(set(response_ids))

    def _validated_arguments(self, event: ActionEvent) -> object:
        """An invalid call's arguments as validation saw them.

        The agent strips the SDK's ``security_risk`` and ``summary`` fields
        before validating, so rewording them does not change the call.
        """
        try:
            arguments = json.loads(event.tool_call.arguments)
        except (ValueError, TypeError):
            return event.tool_call.arguments
        if not isinstance(arguments, dict):
            return arguments
        arguments.pop("security_risk", None)
        if not self._tool_has_summary_param(event.tool_name):
            arguments.pop("summary", None)
        return arguments

    def _tool_has_summary_param(self, tool_name: str) -> bool:
        from openhands.sdk.agent.agent import _tool_has_summary_param

        try:
            tool = self.state.agent.tools_map.get(tool_name)
        except RuntimeError:
            return False
        return tool is not None and _tool_has_summary_param(tool)

    def _is_stuck_repeating_action_error(
        self, last_actions: list[Event], last_observations: list[Event]
    ) -> bool:
        # scenario 2: same action, errors — one repeat past the threshold
        threshold = self.action_error_threshold
        if self._action_error_streak(last_actions, last_observations) > threshold:
            logger.warning("Action, Error loop detected")
            return True
        return False

    def get_action_error_nudge(self) -> str | None:
        """Nudge text once an action-error streak first hits the threshold.

        Nudges once per streak: if the streak is still frozen on the same
        error event (e.g. an empty/reasoning-only response added no new
        action) we've already nudged for it, so we don't re-fire.
        """
        threshold = self.action_error_threshold
        last_actions, last_observations = self._action_error_pairs()
        if self._action_error_streak(last_actions, last_observations) != threshold:
            return None

        action = last_actions[0]
        assert isinstance(action, ActionEvent)
        error = next(
            observation
            for observation in last_observations
            if isinstance(observation, AgentErrorEvent)
            and observation.tool_call_id == action.tool_call_id
        )
        assert isinstance(error, AgentErrorEvent)

        if error.id == self._last_nudged_error_event_id:
            return None
        self._last_nudged_error_event_id = error.id

        if action.action is None and error.validation_errors:
            return (
                f"Your calls to `{action.tool_name}` have failed validation "
                f"{threshold} times without resolving the failing fields. "
                f"Latest error: {error.error}. Correct the invalid or missing "
                "fields or try a different approach before calling this tool again."
            )

        return (
            f"You've called `{action.tool_name}` with the same arguments "
            f"{threshold} times in a row and gotten the same error each "
            f"time: {error.error}. Repeating the exact same call again "
            "will not work — review the error message and either correct "
            "the arguments or try a different approach."
        )

    def _is_stuck_monologue(self, events: list[Event]) -> bool:
        # scenario 3: monologue
        # check for repeated MessageActions with source=AGENT
        # see if the agent is engaged in a good old monologue, telling
        # itself the same thing over and over
        threshold = self.monologue_threshold
        if len(events) < threshold:
            return False

        # Look for N consecutive agent messages without user interruption
        agent_message_count = 0

        for event in reversed(events):
            if isinstance(event, MessageEvent):
                if event.source == "agent":
                    agent_message_count += 1
                elif event.source == "user":
                    break  # User interrupted, not a monologue
            elif isinstance(event, CondensationSummaryEvent):
                # Condensation events don't break the monologue pattern
                continue
            else:
                # Other events (actions/observations) don't count as monologue
                break

        return agent_message_count >= threshold

    def _is_stuck_alternating_action_observation(self, events: list[Event]) -> bool:
        # scenario 4: alternating action-observation loop
        threshold = self.alternating_pattern_threshold

        last_actions: list[Event] = []
        last_observations: list[Event] = []

        # collect most recent N actions and N observations
        for event in reversed(events):
            if isinstance(event, ActionEvent) and len(last_actions) < threshold:
                last_actions.append(event)
            elif (
                isinstance(event, (ObservationEvent, AgentErrorEvent))
                and len(last_observations) < threshold
            ):
                last_observations.append(event)

            if len(last_actions) == threshold and len(last_observations) == threshold:
                break

        if self._invalid_calls_share_response(last_actions):
            return False

        if len(last_actions) == threshold and len(last_observations) == threshold:
            # Check alternating pattern: [A, B, A, B, A, B] where even/odd match
            actions_equal = all(
                self._event_eq(last_actions[i], last_actions[i + 2])
                for i in range(threshold - 2)
            )
            observations_equal = all(
                self._event_eq(last_observations[i], last_observations[i + 2])
                for i in range(threshold - 2)
            )

            if actions_equal and observations_equal:
                logger.warning("Alternating Action, Observation loop detected")
                return True

        return False

    def _is_stuck_context_window_error(self, _events: list[Event]) -> bool:
        """Detects if we are stuck in a loop of context window errors.

        This happens when we repeatedly get context window errors and try to trim,
        but the trimming does not work, causing us to get more context window errors.
        The pattern is repeated AgentCondensationObservation events without any other
        events between them.
        """
        # TODO: blocked by https://github.com/OpenHands/agent-sdk/issues/282
        return False

    def _event_eq(self, event1: Event, event2: Event) -> bool:
        """
        Compare two events for equality, ignoring irrelevant
        details like ids, metrics.
        """
        # Must be same type
        if type(event1) is not type(event2):
            return False

        # For ActionEvents, compare the action content, ignoring IDs
        if isinstance(event1, ActionEvent) and isinstance(event2, ActionEvent):
            if event1.action is None and event2.action is None:
                return (
                    event1.source == event2.source
                    and event1.tool_name == event2.tool_name
                    and self._validated_arguments(event1)
                    == self._validated_arguments(event2)
                )
            return (
                event1.source == event2.source
                and event1.thought == event2.thought
                and event1.action == event2.action
                and event1.tool_name == event2.tool_name
                # Ignore tool_call_id, llm_response_id, action_id as they vary
            )

        # For ObservationEvents, compare the observation content, ignoring IDs
        if isinstance(event1, ObservationEvent) and isinstance(
            event2, ObservationEvent
        ):
            return (
                event1.source == event2.source
                and event1.observation == event2.observation
                and event1.tool_name == event2.tool_name
                # Ignore action_id, tool_call_id as they vary
            )

        # For AgentErrorEvents, compare the error content
        if isinstance(event1, AgentErrorEvent) and isinstance(event2, AgentErrorEvent):
            return (
                event1.source == event2.source and event1.error == event2.error
                # Ignore action_id as it varies
            )

        # For MessageEvents, compare the message content
        if isinstance(event1, MessageEvent) and isinstance(event2, MessageEvent):
            return (
                event1.source == event2.source
                and event1.llm_message == event2.llm_message
            )

        # Default fallback
        return event1 == event2
