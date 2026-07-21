"""Bridges conversation events onto the telemetry sink.

Two properties are non-negotiable here:

* **Total.** Every public method wraps its whole body in ``try/except``.
  ``PubSub`` isolates subscriber failures, but ``EventService.subscribe_to_events``
  awaits an initial state push and only catches ``TimeoutError``, so an
  exception escaping this subscriber during registration would fail
  conversation *startup*.
* **Non-blocking.** ``__call__`` must be ``async def`` to satisfy the
  ``Subscriber`` ABC, but its body contains **zero ``await``** — it terminates
  in the synchronous ``sink.emit``. ``PubSub.__call__`` awaits its subscribers,
  so anything slower would stall event fan-out for the whole conversation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from openhands.agent_server.pub_sub import Subscriber
from openhands.agent_server.telemetry import models as m
from openhands.agent_server.telemetry.factory import DiagnosticEventFactory
from openhands.agent_server.telemetry.sanitizer import (
    COST_BOUNDS,
    COUNT_BOUNDS,
    DURATION_BOUNDS,
    TOKEN_BOUNDS,
    UNKNOWN_TOKEN,
    bucket,
    normalize_error_code,
    safe_token,
)
from openhands.agent_server.telemetry.sink import TelemetrySink
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import AgentErrorEvent, ConversationStateUpdateEvent, Event
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.conversation_state import FULL_STATE_KEY
from openhands.sdk.logger import get_logger
from openhands.sdk.utils import utc_now


logger = get_logger(__name__)

# Derived from the SDK enum rather than hardcoded, so a new terminal status
# cannot silently stop being reported.
_TERMINAL_STATUSES: Final = frozenset(
    s.value for s in ConversationExecutionStatus if s.is_terminal()
)
_FAILURE_STATUSES: Final = _TERMINAL_STATUSES - {
    ConversationExecutionStatus.FINISHED.value
}
_EXECUTION_STATUS_KEY: Final = "execution_status"


@dataclass(frozen=True, slots=True)
class ConversationTelemetryContext:
    """The sanitized facts about one conversation, resolved once at start."""

    conversation_ref: str
    user_id: str | None
    llm_model_family: str
    agent_kind: str
    tool_count: int
    is_fork: bool
    has_agent_profile: bool
    workspace_kind: str
    confirmation_policy: str


@dataclass(slots=True)
class TelemetrySubscriber(Subscriber[Event]):
    """Emits lifecycle and failure events for a single conversation."""

    conversation_id: UUID
    sink: TelemetrySink
    factory: DiagnosticEventFactory
    context: ConversationTelemetryContext

    _started_at: datetime = field(default_factory=utc_now)
    _event_count: int = 0
    _terminal_emitted: bool = False
    _last_status: str | None = None
    _seeded: bool = False
    _total_tokens: int | None = None
    _total_cost: float | None = None

    async def __call__(self, event: Event) -> None:
        try:
            self._handle(event)
        except Exception:
            logger.debug("Telemetry subscriber failed to handle event", exc_info=True)

    def _handle(self, event: Event) -> None:
        self._event_count += 1

        if isinstance(event, AgentErrorEvent):
            self._emit_error_from_agent_event(event)
            return

        if isinstance(event, ConversationErrorEvent):
            self._emit_error_from_conversation_event(event)
            return

        if isinstance(event, ConversationStateUpdateEvent):
            status = _extract_status(event)
            if status is not None:
                self._last_status = status

                if not self._seeded:
                    # The attach-time push is a baseline, not a transition;
                    # otherwise every rehydration re-emits a terminal event.
                    self._seeded = True
                    if status in _TERMINAL_STATUSES:
                        self._terminal_emitted = True
                    return

                if status in _TERMINAL_STATUSES:
                    self._capture_usage(event)
                    self._emit_terminal(status)

    def emit_started(self) -> None:
        """Emit ``conversation_started``. Called once, at registration."""
        try:
            properties = m.ConversationStartedProperties(
                conversation_ref=self.context.conversation_ref,
                llm_model_family=self.context.llm_model_family,
                agent_kind=self.context.agent_kind,
                tool_count=self.context.tool_count,
                is_fork=self.context.is_fork,
                has_agent_profile=self.context.has_agent_profile,
                workspace_kind=self.context.workspace_kind,
                confirmation_policy=self.context.confirmation_policy,
            )
            self.sink.emit(
                self.factory.build(
                    m.EventName.CONVERSATION_STARTED,
                    properties,
                    user_id=self.context.user_id,
                )
            )
        except Exception:
            logger.debug("Telemetry failed to emit conversation_started", exc_info=True)

    def _emit_terminal(self, status: str) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True

        token = safe_token(status, default=UNKNOWN_TOKEN)
        elapsed = (utc_now() - self._started_at).total_seconds()
        properties = m.ConversationOutcomeProperties(
            conversation_ref=self.context.conversation_ref,
            terminal_status=token,
            duration_bucket=bucket(elapsed, DURATION_BOUNDS),
            event_count_bucket=bucket(self._event_count, COUNT_BOUNDS),
            total_tokens_bucket=bucket(self._total_tokens, TOKEN_BOUNDS),
            cost_bucket=bucket(self._total_cost, COST_BOUNDS),
            llm_model_family=self.context.llm_model_family,
        )
        event_name = (
            m.EventName.CONVERSATION_FAILED
            if token in _FAILURE_STATUSES
            else m.EventName.CONVERSATION_FINISHED
        )
        self.sink.emit(
            self.factory.build(event_name, properties, user_id=self.context.user_id)
        )

    def _capture_usage(self, event: ConversationStateUpdateEvent) -> None:
        """Pull aggregate token/cost out of the terminal state snapshot.

        ``full_state`` carries ``stats.usage_to_metrics``, a map of usage id to
        a ``MetricsSnapshot``. Summing it gives the conversation totals, which
        are then bucketed — the raw figures are never reported. Best-effort:
        any shape change degrades to ``unknown`` rather than raising.
        """
        try:
            value = getattr(event, "value", None)
            if not isinstance(value, dict):
                return None
            stats = value.get("stats")
            if not isinstance(stats, dict):
                return None
            per_usage = stats.get("usage_to_metrics")
            if not isinstance(per_usage, dict):
                return None

            tokens = 0
            cost = 0.0
            for snapshot in per_usage.values():
                if not isinstance(snapshot, dict):
                    continue
                cost += float(snapshot.get("accumulated_cost") or 0.0)
                usage = snapshot.get("accumulated_token_usage")
                if isinstance(usage, dict):
                    tokens += int(usage.get("prompt_tokens") or 0)
                    tokens += int(usage.get("completion_tokens") or 0)
            self._total_tokens = tokens
            self._total_cost = cost
        except Exception:
            logger.debug("Could not read usage from state snapshot", exc_info=True)

    def _emit_error_from_agent_event(self, event: AgentErrorEvent) -> None:
        """Report a tool-level agent error.

        Only ``tool_name`` is read. ``AgentErrorEvent.error`` is the scaffold's
        message and routinely contains tool output, paths and model text, so it
        is never touched.
        """
        fingerprint = normalize_error_code("AgentError")
        properties = m.ErrorProperties(
            conversation_ref=self.context.conversation_ref,
            error_class=fingerprint.error_class,
            error_category="tool_execution",
            error_fingerprint=fingerprint.error_fingerprint,
            is_first_party=True,
            is_terminal=False,
            tool_name=safe_token(getattr(event, "tool_name", None)),
        )
        self.sink.emit(
            self.factory.build(
                m.EventName.CONVERSATION_ERROR, properties, user_id=self.context.user_id
            )
        )

    def _emit_error_from_conversation_event(
        self, event: ConversationErrorEvent
    ) -> None:
        """Report a conversation-level failure.

        Only ``code`` is read — its docstring documents it as "typically a
        type". The sibling ``detail`` field is free-form prose and is never
        touched.
        """
        fingerprint = normalize_error_code(getattr(event, "code", None))
        properties = m.ErrorProperties(
            conversation_ref=self.context.conversation_ref,
            error_class=fingerprint.error_class,
            error_category=fingerprint.error_category,
            error_fingerprint=fingerprint.error_fingerprint,
            is_first_party=True,
            is_terminal=True,
        )
        self.sink.emit(
            self.factory.build(
                m.EventName.CONVERSATION_ERROR, properties, user_id=self.context.user_id
            )
        )

    async def close(self) -> None:
        """Emit a terminal event if none was observed.

        Deliberately does **not** close ``self.sink``: the sink is shared
        process-wide and outlives every conversation.
        """
        try:
            if not self._terminal_emitted:
                self._emit_terminal(self._last_status or UNKNOWN_TOKEN)
        except Exception:
            logger.debug("Telemetry subscriber failed to close", exc_info=True)


def _extract_status(event: ConversationStateUpdateEvent) -> str | None:
    """Pull ``execution_status`` out of a state update, if present."""
    key = getattr(event, "key", None)
    value: Any = getattr(event, "value", None)

    if key == _EXECUTION_STATUS_KEY and isinstance(value, str):
        return value
    if key == FULL_STATE_KEY and isinstance(value, dict):
        status = value.get(_EXECUTION_STATUS_KEY)
        if isinstance(status, str):
            return status
    return None


__all__ = [
    "ConversationTelemetryContext",
    "TelemetrySubscriber",
]
