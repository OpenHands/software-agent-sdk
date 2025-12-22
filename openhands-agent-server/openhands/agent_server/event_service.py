import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from openhands.agent_server.models import (
    ConfirmationResponseRequest,
    EventPage,
    EventSortOrder,
    StoredConversation,
)
from openhands.agent_server.pub_sub import PubSub, Subscriber
from openhands.agent_server.utils import utc_now
from openhands.sdk import LLM, Agent, AgentBase, Event, Message, get_logger
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.secret_registry import SecretValue
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.event.llm_completion_log import LLMCompletionLogEvent
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import ConfirmationPolicyBase
from openhands.sdk.utils.async_utils import AsyncCallbackWrapper
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


logger = get_logger(__name__)


@dataclass
class EventService:
    """
    Event service for a conversation running locally, analogous to a conversation
    in the SDK. Async mostly for forward compatibility
    """

    stored: StoredConversation
    conversations_dir: Path
    cipher: Cipher | None = None
    _conversation: LocalConversation | None = field(default=None, init=False)
    _pub_sub: PubSub[Event] = field(default_factory=lambda: PubSub[Event](), init=False)
    _run_task: asyncio.Task | None = field(default=None, init=False)
    _run_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def conversation_dir(self):
        return self.conversations_dir / self.stored.id.hex

    async def load_meta(self):
        meta_file = self.conversation_dir / "meta.json"
        self.stored = StoredConversation.model_validate_json(
            meta_file.read_text(),
            context={
                "cipher": self.cipher,
            },
        )

    async def save_meta(self):
        self.stored.updated_at = utc_now()
        meta_file = self.conversation_dir / "meta.json"
        meta_file.write_text(
            self.stored.model_dump_json(
                context={
                    "cipher": self.cipher,
                }
            )
        )

    def get_conversation(self):
        if not self._conversation:
            raise ValueError("inactive_service")
        return self._conversation

    def _get_event_sync(self, event_id: str) -> Event | None:
        """Private sync function to get event with state lock."""
        if not self._conversation:
            raise ValueError("inactive_service")
        with self._conversation._state as state:
            index = state.events.get_index(event_id)
            event = state.events[index]
            return event

    async def get_event(self, event_id: str) -> Event | None:
        if not self._conversation:
            raise ValueError("inactive_service")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_event_sync, event_id)

    def _search_events_sync(
        self,
        page_id: str | None = None,
        limit: int = 100,
        kind: str | None = None,
        source: str | None = None,
        body: str | None = None,
        sort_order: EventSortOrder = EventSortOrder.TIMESTAMP,
        timestamp__gte: datetime | None = None,
        timestamp__lt: datetime | None = None,
    ) -> EventPage:
        """Private sync function to search events with state lock."""
        if not self._conversation:
            raise ValueError("inactive_service")

        # Convert datetime to ISO string for comparison (ISO strings are comparable)
        timestamp_gte_str = timestamp__gte.isoformat() if timestamp__gte else None
        timestamp_lt_str = timestamp__lt.isoformat() if timestamp__lt else None

        # Collect all events
        all_events = []
        with self._conversation._state as state:
            for event in state.events:
                # Apply kind filter if provided
                if (
                    kind is not None
                    and f"{event.__class__.__module__}.{event.__class__.__name__}"
                    != kind
                ):
                    continue

                # Apply source filter if provided
                if source is not None and event.source != source:
                    continue

                # Apply body filter if provided (case-insensitive substring match)
                if body is not None:
                    if not self._event_matches_body(event, body):
                        continue

                # Apply timestamp filters if provided (ISO string comparison)
                if (
                    timestamp_gte_str is not None
                    and event.timestamp < timestamp_gte_str
                ):
                    continue
                if timestamp_lt_str is not None and event.timestamp >= timestamp_lt_str:
                    continue

                all_events.append(event)

        # Sort events based on sort_order
        if sort_order == EventSortOrder.TIMESTAMP:
            all_events.sort(key=lambda x: x.timestamp)
        elif sort_order == EventSortOrder.TIMESTAMP_DESC:
            all_events.sort(key=lambda x: x.timestamp, reverse=True)

        # Handle pagination
        items = []
        start_index = 0

        # Find the starting point if page_id is provided
        if page_id:
            for i, event in enumerate(all_events):
                if event.id == page_id:
                    start_index = i
                    break

        # Collect items for this page
        next_page_id = None
        for i in range(start_index, len(all_events)):
            if len(items) >= limit:
                # We have more items, set next_page_id
                if i < len(all_events):
                    next_page_id = all_events[i].id
                break
            items.append(all_events[i])

        return EventPage(items=items, next_page_id=next_page_id)

    async def search_events(
        self,
        page_id: str | None = None,
        limit: int = 100,
        kind: str | None = None,
        source: str | None = None,
        body: str | None = None,
        sort_order: EventSortOrder = EventSortOrder.TIMESTAMP,
        timestamp__gte: datetime | None = None,
        timestamp__lt: datetime | None = None,
    ) -> EventPage:
        if not self._conversation:
            raise ValueError("inactive_service")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._search_events_sync,
            page_id,
            limit,
            kind,
            source,
            body,
            sort_order,
            timestamp__gte,
            timestamp__lt,
        )

    def _count_events_sync(
        self,
        kind: str | None = None,
        source: str | None = None,
        body: str | None = None,
        timestamp__gte: datetime | None = None,
        timestamp__lt: datetime | None = None,
    ) -> int:
        """Private sync function to count events with state lock."""
        if not self._conversation:
            raise ValueError("inactive_service")

        # Convert datetime to ISO string for comparison (ISO strings are comparable)
        timestamp_gte_str = timestamp__gte.isoformat() if timestamp__gte else None
        timestamp_lt_str = timestamp__lt.isoformat() if timestamp__lt else None

        count = 0
        with self._conversation._state as state:
            for event in state.events:
                # Apply kind filter if provided
                if (
                    kind is not None
                    and f"{event.__class__.__module__}.{event.__class__.__name__}"
                    != kind
                ):
                    continue

                # Apply source filter if provided
                if source is not None and event.source != source:
                    continue

                # Apply body filter if provided (case-insensitive substring match)
                if body is not None:
                    if not self._event_matches_body(event, body):
                        continue

                # Apply timestamp filters if provided (ISO string comparison)
                if (
                    timestamp_gte_str is not None
                    and event.timestamp < timestamp_gte_str
                ):
                    continue
                if timestamp_lt_str is not None and event.timestamp >= timestamp_lt_str:
                    continue

                count += 1

        return count

    async def count_events(
        self,
        kind: str | None = None,
        source: str | None = None,
        body: str | None = None,
        timestamp__gte: datetime | None = None,
        timestamp__lt: datetime | None = None,
    ) -> int:
        """Count events matching the given filters."""
        if not self._conversation:
            raise ValueError("inactive_service")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._count_events_sync,
            kind,
            source,
            body,
            timestamp__gte,
            timestamp__lt,
        )

    def _event_matches_body(self, event: Event, body: str) -> bool:
        """Check if event's message content matches body filter (case-insensitive)."""
        # Import here to avoid circular imports
        from openhands.sdk.event.llm_convertible.message import MessageEvent
        from openhands.sdk.llm.message import content_to_str

        # Only check MessageEvent instances for body content
        if not isinstance(event, MessageEvent):
            return False

        # Extract text content from the message
        text_parts = content_to_str(event.llm_message.content)

        # Also check extended content if present
        if event.extended_content:
            extended_text_parts = content_to_str(event.extended_content)
            text_parts.extend(extended_text_parts)

        # Also check reasoning content if present
        if event.reasoning_content:
            text_parts.append(event.reasoning_content)

        # Combine all text content and perform case-insensitive substring match
        full_text = " ".join(text_parts).lower()
        return body.lower() in full_text

    async def batch_get_events(self, event_ids: list[str]) -> list[Event | None]:
        """Given a list of ids, get events (Or none for any which were not found)"""
        results = await asyncio.gather(
            *[self.get_event(event_id) for event_id in event_ids]
        )
        return results

    async def send_message(self, message: Message, run: bool = False):
        if not self._conversation:
            raise ValueError("inactive_service")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._conversation.send_message, message)
        if run:
            with self._conversation.state as state:
                run = state.execution_status != ConversationExecutionStatus.RUNNING
        if run:
            loop.run_in_executor(None, self._conversation.run)

    async def subscribe_to_events(self, subscriber: Subscriber[Event]) -> UUID:
        subscriber_id = self._pub_sub.subscribe(subscriber)

        # Send current state to the new subscriber immediately
        if self._conversation:
            state = self._conversation._state
            with state:
                # Create state update event with current state information
                state_update_event = (
                    ConversationStateUpdateEvent.from_conversation_state(state)
                )

                # Send state update directly to the new subscriber
                try:
                    await subscriber(state_update_event)
                except Exception as e:
                    logger.error(
                        f"Error sending initial state to subscriber "
                        f"{subscriber_id}: {e}"
                    )

        return subscriber_id

    async def unsubscribe_from_events(self, subscriber_id: UUID) -> bool:
        return self._pub_sub.unsubscribe(subscriber_id)

    def _emit_event_from_thread(self, event: Event) -> None:
        """Helper to safely emit events from non-async contexts (e.g., callbacks).

        This schedules event emission in the main event loop, making it safe to call
        from callbacks that may run in different threads. Events are emitted through
        the conversation's normal event flow to ensure they are persisted.
        """
        if self._main_loop and self._main_loop.is_running() and self._conversation:
            # Capture conversation reference for closure
            conversation = self._conversation

            # Wrap _on_event with lock acquisition to ensure thread-safe access
            # to conversation state and event log during concurrent operations
            def locked_on_event():
                with conversation._state:
                    conversation._on_event(event)

            # Run the locked callback in an executor to ensure the event is
            # both persisted and sent to WebSocket subscribers
            self._main_loop.run_in_executor(None, locked_on_event)

    def _setup_llm_log_streaming(self, agent: AgentBase) -> None:
        """Configure LLM log callbacks to stream logs via events."""
        for llm in agent.get_all_llms():
            if not llm.log_completions:
                continue

            # Capture variables for closure
            usage_id = llm.usage_id
            model_name = llm.model

            def log_callback(
                filename: str, log_data: str, uid=usage_id, model=model_name
            ) -> None:
                """Callback to emit LLM completion logs as events."""
                event = LLMCompletionLogEvent(
                    filename=filename,
                    log_data=log_data,
                    model_name=model,
                    usage_id=uid,
                )
                self._emit_event_from_thread(event)

            llm.telemetry.set_log_completions_callback(log_callback)

    def _setup_stats_streaming(self, agent: AgentBase) -> None:
        """Configure stats update callbacks to stream stats changes via events."""

        def stats_callback() -> None:
            """Callback to emit stats updates."""
            # Publish only the stats field to avoid sending entire state
            if not self._conversation:
                return
            state = self._conversation._state
            with state:
                event = ConversationStateUpdateEvent(key="stats", value=state.stats)
            self._emit_event_from_thread(event)

        for llm in agent.get_all_llms():
            llm.telemetry.set_stats_update_callback(stats_callback)

    async def start(self):
        # Store the main event loop for cross-thread communication
        self._main_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

        # self.stored contains an Agent configuration we can instantiate
        self.conversation_dir.mkdir(parents=True, exist_ok=True)
        workspace = self.stored.workspace
        assert isinstance(workspace, LocalWorkspace)
        Path(workspace.working_dir).mkdir(parents=True, exist_ok=True)
        agent = Agent.model_validate(
            self.stored.agent.model_dump(context={"expose_secrets": True}),
        )

        conversation = LocalConversation(
            agent=agent,
            workspace=workspace,
            persistence_dir=str(self.conversations_dir),
            conversation_id=self.stored.id,
            callbacks=[
                AsyncCallbackWrapper(self._pub_sub, loop=asyncio.get_running_loop())
            ],
            max_iteration_per_run=self.stored.max_iterations,
            stuck_detection=self.stored.stuck_detection,
            visualizer=None,
            secrets=self.stored.secrets,
        )

        # Set confirmation mode if enabled
        conversation.set_confirmation_policy(self.stored.confirmation_policy)
        self._conversation = conversation

        # Register state change callback to automatically publish updates
        self._conversation._state.set_on_state_change(self._conversation._on_event)

        # Setup LLM log streaming for remote execution
        self._setup_llm_log_streaming(self._conversation.agent)

        # Setup stats streaming for remote execution
        self._setup_stats_streaming(self._conversation.agent)

        # Publish initial state update
        await self._publish_state_update()

    async def run(self):
        """Run the conversation asynchronously in the background.

        This method starts the conversation run in a background task and returns
        immediately. The conversation status can be monitored via the
        GET /api/conversations/{id} endpoint or WebSocket events.

        Raises:
            ValueError: If the service is inactive or conversation is already running.
        """
        if not self._conversation:
            raise ValueError("inactive_service")

        # Use lock to make check-and-set atomic, preventing race conditions
        async with self._run_lock:
            # Check if already running
            with self._conversation._state as state:
                if state.execution_status == ConversationExecutionStatus.RUNNING:
                    raise ValueError("conversation_already_running")

            # Check if there's already a running task
            if self._run_task is not None and not self._run_task.done():
                raise ValueError("conversation_already_running")

            # Capture conversation reference for the closure
            conversation = self._conversation

            # Start run in background
            loop = asyncio.get_running_loop()

            async def _run_and_publish():
                try:
                    await loop.run_in_executor(None, conversation.run)
                except Exception as e:
                    logger.error(f"Error during conversation run: {e}")
                finally:
                    # Clear task reference and publish state update
                    self._run_task = None
                    await self._publish_state_update()

            # Create task but don't await it - runs in background
            self._run_task = asyncio.create_task(_run_and_publish())

    async def respond_to_confirmation(self, request: ConfirmationResponseRequest):
        if request.accept:
            try:
                await self.run()
            except ValueError as e:
                # Treat "already running" as a no-op success
                if str(e) == "conversation_already_running":
                    logger.debug(
                        "Confirmation accepted but conversation already running"
                    )
                else:
                    raise
        else:
            await self.reject_pending_actions(request.reason)

    async def reject_pending_actions(self, reason: str):
        """Reject all pending actions and publish updated state."""
        if not self._conversation:
            raise ValueError("inactive_service")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._conversation.reject_pending_actions, reason
        )

    async def pause(self):
        if self._conversation:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._conversation.pause)
            # Publish state update after pause to ensure stats are updated
            await self._publish_state_update()

    async def update_secrets(self, secrets: dict[str, SecretValue]):
        """Update secrets in the conversation."""
        if not self._conversation:
            raise ValueError("inactive_service")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._conversation.update_secrets, secrets)

    async def set_confirmation_policy(self, policy: ConfirmationPolicyBase):
        """Set the confirmation policy for the conversation."""
        if not self._conversation:
            raise ValueError("inactive_service")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._conversation.set_confirmation_policy, policy
        )

    async def set_security_analyzer(
        self, security_analyzer: SecurityAnalyzerBase | None
    ):
        """Set the security analyzer for the conversation."""
        if not self._conversation:
            raise ValueError("inactive_service")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._conversation.set_security_analyzer, security_analyzer
        )

    async def close(self):
        await self._pub_sub.close()
        if self._conversation:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self._conversation.close)

    async def generate_title(
        self, llm: "LLM | None" = None, max_length: int = 50
    ) -> str:
        """Generate a title for the conversation.

        Resolves the provided LLM via the conversation's registry if a usage_id is
        present, registering it if needed. Then delegates to LocalConversation in an
        executor to avoid blocking the event loop.
        """
        if not self._conversation:
            raise ValueError("inactive_service")

        resolved_llm = llm
        if llm is not None:
            usage_id = llm.usage_id
            try:
                resolved_llm = self._conversation.llm_registry.get(usage_id)
            except KeyError:
                self._conversation.llm_registry.add(llm)
                resolved_llm = llm

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._conversation.generate_title, resolved_llm, max_length
        )

    async def ask_agent(self, question: str) -> str:
        """Ask the agent a simple question without affecting conversation state.

        Delegates to LocalConversation in an executor to avoid blocking the event loop.
        """
        if not self._conversation:
            raise ValueError("inactive_service")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._conversation.ask_agent, question)

    async def condense(self) -> None:
        """Force condensation of the conversation history.

        Delegates to LocalConversation in an executor to avoid blocking the event loop.
        """
        if not self._conversation:
            raise ValueError("inactive_service")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._conversation.condense)

    async def get_state(self) -> ConversationState:
        if not self._conversation:
            raise ValueError("inactive_service")
        return self._conversation._state

    async def _publish_state_update(self):
        """Publish a ConversationStateUpdateEvent with the current state."""
        if not self._conversation:
            return

        state = self._conversation._state
        with state:
            state_update_event = ConversationStateUpdateEvent.from_conversation_state(
                state
            )
            await self._pub_sub(state_update_event)

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.save_meta()
        await self.close()

    def is_open(self) -> bool:
        return bool(self._conversation)
