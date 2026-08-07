"""SnapshotReplayAgent implementation."""

import json
import os
from collections.abc import Sequence
from typing import Any

from pydantic import Field, PrivateAttr

from openhands.sdk.agent.agent import Agent, _ActionBatch
from openhands.sdk.conversation import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
    LocalConversation,
)
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import ActionEvent, Event, MessageEvent, ObservationEvent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class SnapshotReplayAgent(Agent):
    """An agent that replays pre-recorded actions from a snapshot.

    Bypasses the LLM and executes pre-recorded ``ActionEvent``s against the live
    environment, collecting real ``ObservationEvent``s to verify tool behavior
    deterministically. Useful for regression testing without LLM overhead.
    """

    replay_mode: bool = Field(default=False, description="Enable replay mode")
    replay_persistence: str | None = Field(
        default=None, description="Path to persistence dir to load snapshot from"
    )
    replay_snapshot: list[Event] | None = Field(
        default=None, description="In-memory list of snapshot events to fallback to"
    )
    replay_conversation_id: str | None = Field(
        default=None,
        description="Optional conversation ID to replay from a base path",
    )
    drift_log_path: str | None = Field(
        default=None,
        description="Path to log any drifts between actual and expected observations",
    )

    _actual_replay_mode: bool = PrivateAttr(default=False)
    _replay_events: list[Event] = PrivateAttr(default_factory=list)
    _current_event_idx: int = PrivateAttr(default=0)
    _expected_observations: dict[str, list[ObservationEvent]] = PrivateAttr(
        default_factory=dict
    )

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)

        if self.replay_mode:
            loaded_events: list[Event] = []

            if self.replay_persistence:
                try:
                    from openhands.sdk.conversation import BaseConversation
                    from openhands.sdk.conversation.event_store import EventLog
                    from openhands.sdk.io import LocalFileStore

                    # Resolve the actual path
                    resolved_path = self.replay_persistence
                    if self.replay_conversation_id:
                        # Use get_persistence_dir logic if ID is provided
                        import uuid

                        try:
                            conv_id = uuid.UUID(self.replay_conversation_id)
                            resolved_path = BaseConversation.get_persistence_dir(
                                self.replay_persistence, conv_id
                            )
                        except ValueError:
                            # Fallback if ID is not a valid UUID string
                            resolved_path = os.path.join(
                                self.replay_persistence, self.replay_conversation_id
                            )
                    else:
                        # Discovery logic: find the actual conversation subdirectory
                        resolved_path = (
                            self._resolve_persistence_path(self.replay_persistence)
                            or self.replay_persistence
                        )

                    fs = LocalFileStore(resolved_path)
                    loaded_events = list(EventLog(fs))
                    logger.info(
                        f"Loaded snapshot from {resolved_path}. "
                        f"Number of events: {len(loaded_events)}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to load snapshot from {self.replay_persistence}: {e}"
                    )

            if not loaded_events and self.replay_snapshot:
                loaded_events = self.replay_snapshot
                logger.info("Loaded snapshot from in-memory replay_snapshot")

            if loaded_events:
                self._actual_replay_mode = True
                self._prepare_replay_events(loaded_events)
            else:
                logger.warning(
                    "Replay mode enabled but no snapshot found. "
                    "Falling back to real LLM calls."
                )
                self._actual_replay_mode = False
        else:
            self._actual_replay_mode = False

    def _prepare_replay_events(self, loaded_events: list[Event]) -> None:
        self._replay_events = []
        self._expected_observations = {}
        for event in loaded_events:
            if isinstance(event, ActionEvent):
                self._replay_events.append(event)
            elif isinstance(event, MessageEvent) and event.source == "agent":
                self._replay_events.append(event)
            elif isinstance(event, ObservationEvent) and event.action_id:
                if event.action_id not in self._expected_observations:
                    self._expected_observations[event.action_id] = []
                self._expected_observations[event.action_id].append(event)

    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        if not self._actual_replay_mode:
            logger.debug(
                "[SnapshotReplayAgent]Not in replay mode. Executing LLM call.."
            )
            return super().step(conversation, on_event, on_token)

        state = conversation.state

        pending_actions = ConversationState.get_unmatched_actions(state.events)
        if pending_actions:
            logger.debug(
                f"Executing {len(pending_actions)} pending actions with drift check"
            )
            self._execute_actions_with_drift_check(
                conversation, pending_actions, on_event
            )
            return

        if self._current_event_idx >= len(self._replay_events):
            logger.info("Replay script exhausted.")
            state.execution_status = ConversationExecutionStatus.FINISHED
            return

        event = self._replay_events[self._current_event_idx]
        logger.info(
            f"Processing replay event {self._current_event_idx}/"
            f"{len(self._replay_events)}: {type(event).__name__}"
        )
        self._current_event_idx += 1

        if isinstance(event, ActionEvent):
            logger.info(
                f"Replaying ActionEvent: tool={event.tool_name}, action_id={event.id}"
            )
            # Strip LLM-specific fields for a cleaner replay log
            event = event.model_copy(
                update={
                    "summary": None,
                    "reasoning_content": None,
                    "thought": [],
                    "thinking_blocks": [],
                    "responses_reasoning_item": None,
                }
            )
            self._execute_actions_with_drift_check(conversation, [event], on_event)

        elif isinstance(event, MessageEvent):
            logger.debug(f"Replaying MessageEvent: {event.llm_message}...")
            if event.source == "agent":
                # Strip reasoning from agent messages during replay
                new_msg = event.llm_message.model_copy(
                    update={
                        "reasoning_content": None,
                        "thinking_blocks": [],
                        "responses_reasoning_item": None,
                    }
                )
                event = event.model_copy(update={"llm_message": new_msg})
            on_event(event)
            state.execution_status = ConversationExecutionStatus.FINISHED

    def _execute_actions_with_drift_check(
        self,
        conversation: LocalConversation,
        action_events: list[ActionEvent],
        on_event: ConversationCallbackType,
    ) -> None:
        state = conversation.state
        batch = _ActionBatch.prepare(
            action_events,
            state=state,
            executor=self._parallel_executor,
            tool_runner=lambda ae: self._execute_action_event(conversation, ae),
            tools=self.tools_map,
        )
        batch.emit(on_event)

        if self.drift_log_path:
            with open(self.drift_log_path, "a", encoding="utf-8") as f:
                for ae in action_events:
                    actual_obs = batch.results_by_id.get(ae.id, [])
                    expected_obs = self._expected_observations.get(ae.id, [])

                    drift: dict[str, Any] | str = "No drift"
                    if self._check_drift(actual_obs, expected_obs):
                        drift = {
                            "Drift Present": True,
                            "Expected": [
                                obs.model_dump(mode="json") for obs in expected_obs
                            ],
                        }

                    log_entry = {
                        "Action": ae.model_dump(mode="json"),
                        "Actual Observation": [
                            obs.model_dump(mode="json") for obs in actual_obs
                        ],
                        "Drift from expected observation": drift,
                    }
                    f.write(json.dumps(log_entry) + "\n")

        batch.finalize(
            on_event=on_event,
            check_iterative_refinement=lambda ae: (
                self._check_iterative_refinement(conversation, ae)
            ),
            mark_finished=lambda: setattr(
                state,
                "execution_status",
                ConversationExecutionStatus.FINISHED,
            ),
        )

    def _check_drift(self, actual: Sequence[Event], expected: Sequence[Event]) -> bool:
        if len(actual) != len(expected):
            return True

        for a, e in zip(actual, expected):
            a_dict = a.model_dump(mode="json")
            e_dict = e.model_dump(mode="json")

            # Ignore standard metadata fields that differ between runs
            ignore_keys = {"id", "timestamp", "action_id", "tool_call_id"}
            for key in ignore_keys:
                a_dict.pop(key, None)
                e_dict.pop(key, None)

            if a_dict != e_dict:
                return True

        return False

    def _resolve_persistence_path(self, base_path: str) -> str | None:
        """Resolve the actual conversation directory from a base path.

        Tries:
        1. The base_path itself (if it contains an 'events' directory).
        2. Any immediate subdirectory (if it contains an 'events' directory).
        """
        from openhands.sdk.conversation.persistence_const import EVENTS_DIR

        # 1. Check if direct path
        if os.path.exists(os.path.join(base_path, EVENTS_DIR)):
            return base_path

        # 2. Scan subdirectories
        try:
            subdirs = [
                d
                for d in os.listdir(base_path)
                if os.path.isdir(os.path.join(base_path, d))
            ]
            # Pick the first one that looks like a conversation
            for d in subdirs:
                potential = os.path.join(base_path, d)
                if os.path.exists(os.path.join(potential, EVENTS_DIR)):
                    logger.debug(f"Resolved snapshot subdirectory: {potential}")
                    return potential
        except Exception as e:
            logger.debug(f"Error scanning subdirectories in {base_path}: {e}")

        return None
