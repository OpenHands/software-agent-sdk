from collections import defaultdict

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
from openhands.sdk.event.types import EventID
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.schema import Action, Observation


logger = get_logger(__name__)


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

    def is_stuck(self) -> bool:
        """Check if the agent is currently stuck."""
        events = list(self.state.events)

        # Only look at history after the last user message
        last_user_msg_index = next(
            (
                i
                for i in reversed(range(len(events)))
                if isinstance(events[i], MessageEvent) and events[i].source == "user"
            ),
            -1,  # Default to -1 if no user message found
        )
        if last_user_msg_index == -1:
            logger.warning("No user message found in history, skipping stuck detection")
            return False

        events = events[last_user_msg_index + 1 :]

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

        # Collect enough actions and observations for detection
        max_needed = max(self.action_observation_threshold, self.action_error_threshold)
        last_actions: list[Event] = []
        last_observations: list[Event] = []

        # Retrieve the last N actions and observations from the end of history
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

        # Check all stuck patterns
        # scenario 1: same action, same observation
        if self._is_stuck_repeating_action_observation(last_actions, last_observations):
            return True

        # scenario 2: same action, errors
        if self._is_stuck_repeating_action_error(last_actions, last_observations):
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
        # scenario 6:
        if len(events) >= 12:
            if self._is_stuck_repeating_in_recent_group(events):
                return True

        return False

    def _is_stuck_repeating_action_observation(
        self, last_actions: list[Event], last_observations: list[Event]
    ) -> bool:
        # scenario 1: same action, same observation
        threshold = self.action_observation_threshold

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

    def _is_stuck_repeating_action_error(
        self, last_actions: list[Event], last_observations: list[Event]
    ) -> bool:
        # scenario 2: same action, errors
        threshold = self.action_error_threshold
        if len(last_actions) < threshold or len(last_observations) < threshold:
            return False

        # are the last N actions the "same"?
        if all(
            self._event_eq(last_actions[0], action)
            for action in last_actions[:threshold]
        ):
            # and the last N observations are all errors?
            if all(
                isinstance(obs, AgentErrorEvent)
                for obs in last_observations[:threshold]
            ):
                logger.warning("Action, Error loop detected")
                return True

        # Check if observations are errors
        return False

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
        """Detects if we're stuck in a loop of context window errors.

        This happens when we repeatedly get context window errors and try to trim,
        but the trimming doesn't work, causing us to get more context window errors.
        The pattern is repeated AgentCondensationObservation events without any other
        events between them.
        """
        # TODO: blocked by https://github.com/OpenHands/agent-sdk/issues/282
        return False

    def _is_stuck_repeating_in_recent_group(self, events: list[Event]) -> bool:
        """
        Event group-based repetitive loop detection
        Core Logic: Group events by llm_response_id (events from the same round of
        LLM decision form a group),
        count the repetition times of core actions and observations/errors in recent
        event groups,
        and determine if the Agent is trapped in one of the following two loops:
        1. Action-Error Loop: In the latest 3 event groups, the same core action
        repeats ≥3 times AND the same core error repeats ≥3 times;
        2. Action-Observation Loop: In the latest 4 event groups, the same core action
          repeats ≥4 times AND the same core observation repeats ≥4 times.
        Design Purpose: To address the limitations and poor adaptability of the existing
        detection methods in scenarios where the LLM generates multiple actions at once.

        Args:
            events: Input event list (filtered in `is_stuck` to "events after the last
            user message")
        Returns:
            bool: Whether trapped in a repetitive loop (True if stuck, False otherwise)

        """
        # Count dictionaries(key=core feature, value=repeat count)-(Action+Error)
        repeat_err_action_counts: dict[tuple[str, Action | None, str], int] = (
            defaultdict(int)
        )
        repeat_err_obs_counts: dict[tuple[str, str | Observation], int] = defaultdict(
            int
        )
        # Count dictionaries(key=core feature, value=repeat count)-(Action+Observation)
        repeat_action_counts: dict[tuple[str, Action | None, str], int] = defaultdict(
            int
        )
        repeat_obs_counts: dict[tuple[str, str | Observation], int] = defaultdict(int)

        least_group_id: EventID | None = None
        group_num: int = 0

        for event in events:
            if isinstance(event, ActionEvent):
                if least_group_id != event.llm_response_id:
                    least_group_id = event.llm_response_id
                    group_num += 1
                action_key: tuple[str, Action | None, str] = (
                    event.tool_name,
                    event.action,
                    event.source,
                )
                if group_num < 4:
                    repeat_err_action_counts[action_key] += 1
                if group_num > 4:
                    break
                repeat_action_counts[action_key] += 1

            elif isinstance(event, (ObservationEvent, AgentErrorEvent)):
                obs_content: str | Observation = (
                    event.observation
                    if isinstance(event, ObservationEvent)
                    else event.error
                )
                obs_key: tuple[str, str | Observation] = (event.source, obs_content)

                if group_num < 4 and isinstance(event, AgentErrorEvent):
                    repeat_err_obs_counts[obs_key] += 1
                repeat_obs_counts[obs_key] += 1

        if group_num < 3:
            return False
        # When group=3, requiring the size to be greater than 3 indicates that at
        # least one LLM call within the latest 3 groups has generated multiple actions.
        if len(repeat_err_action_counts) > 3 and len(repeat_err_obs_counts) >= 3:
            has_repeat_err_action: bool = any(
                count >= 3 for count in repeat_err_action_counts.values()
            )
            has_repeat_err_obs: bool = any(
                count >= 3 for count in repeat_err_obs_counts.values()
            )

            if has_repeat_err_action and has_repeat_err_obs:
                top_action: tuple[str, Action | None, str] = max(
                    repeat_err_action_counts.items(), key=lambda x: x[1]
                )[0]
                top_obs: tuple[str, str | Observation] = max(
                    repeat_err_obs_counts.items(), key=lambda x: x[1]
                )[0]
                logger.warning(
                    "Repeating Action-AgentErrorEvent loop detected "
                    "(recent 3 groups):"
                    f"Action(tool={top_action[0]}, action={top_action[1]})"
                    f"x{repeat_err_action_counts[top_action]}, "
                    f"AgentErrorEvent(content={top_obs[1]}) "
                    f"x{repeat_err_obs_counts[top_obs]}"
                )
                return True

        # When group=4, requiring the size to be greater than 4 indicates that at
        # least one LLM call within the latest 4 groups has generated multiple actions.
        if len(repeat_action_counts) > 4 and len(repeat_obs_counts) > 4:
            has_repeat_action: bool = any(
                count >= 4 for count in repeat_action_counts.values()
            )
            has_repeat_obs: bool = any(
                count >= 4 for count in repeat_obs_counts.values()
            )

            if has_repeat_action and has_repeat_obs:
                top_action: tuple[str, Action | None, str] = max(
                    repeat_action_counts.items(), key=lambda x: x[1]
                )[0]
                top_obs: tuple[str, str | Observation] = max(
                    repeat_obs_counts.items(), key=lambda x: x[1]
                )[0]
                logger.warning(
                    f"Repeating Action-Observation loop detected (recent 4 groups): "
                    f"Action(tool={top_action[0]}, action={top_action[1]}) "
                    f"x{repeat_action_counts[top_action]}, "
                    f"Observation(content={top_obs[1]}) x{repeat_obs_counts[top_obs]}"
                )
                return True

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
