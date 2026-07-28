import asyncio
import bisect
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from queue import Empty, Queue
from typing import TYPE_CHECKING, SupportsIndex, overload
from urllib.parse import quote, urlparse

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.base import BaseConversation, ConversationStateProtocol


if TYPE_CHECKING:
    from openhands.sdk.tool.schema import Action, Observation
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.events_list_base import EventsListBase
from openhands.sdk.conversation.exceptions import (
    ConversationRunError,
    WebSocketConnectionError,
)
from openhands.sdk.conversation.secret_registry import SecretValue
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.title_utils import generate_conversation_title
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationID,
    StuckDetectionThresholds,
    TraceMetadataValue,
)
from openhands.sdk.conversation.visualizer import (
    ConversationVisualizerBase,
    DefaultConversationVisualizer,
)
from openhands.sdk.event.acp_tool_call import ACPToolCallEvent
from openhands.sdk.event.base import Event
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.conversation_state import (
    FULL_STATE_KEY,
    ConversationStateUpdateEvent,
)
from openhands.sdk.event.llm_completion_log import LLMCompletionLogEvent
from openhands.sdk.event.types import EventID
from openhands.sdk.hooks import HookConfig
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.logger import DEBUG, get_logger
from openhands.sdk.observability.laminar import observe
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    ConfirmationPolicyBase,
)
from openhands.sdk.tool.client_tool import ClientTool, ClientToolSpec
from openhands.sdk.utils.redact import http_error_log_content
from openhands.sdk.workspace import LocalWorkspace, RemoteWorkspace


logger = get_logger(__name__)

LEGACY_CONVERSATIONS_PATH = "/api/conversations"
FATAL_WS_CLOSE_CODES = frozenset({4001, 4004})


def _agent_kind_mismatch_message(conversation_id: ConversationID) -> str:
    return (
        f"Conversation {conversation_id} was started with a different agent kind. "
        "Attach with a matching agent type."
    )


def _validate_remote_agent(agent_data: dict) -> AgentBase:
    if agent_data.get("kind") == "ACPAgent":
        from openhands.sdk.agent.acp_agent import ACPAgent

        return ACPAgent.model_validate(agent_data)
    return AgentBase.model_validate(agent_data)


def _websocket_close_code(exc: ConnectionClosed) -> int | None:
    for close_frame in (exc.rcvd, exc.sent):
        if close_frame is not None:
            return close_frame.code
    return None


def _is_fatal_websocket_close(
    exc: ConnectionClosed,
) -> bool:
    return _websocket_close_code(exc) in FATAL_WS_CLOSE_CODES


def _send_request(
    client: httpx.Client,
    method: str,
    url: str,
    acceptable_status_codes: set[int] | None = None,
    **kwargs,
) -> httpx.Response:
    try:
        response = client.request(method, url, **kwargs)
        if acceptable_status_codes and response.status_code in acceptable_status_codes:
            return response
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as e:
        content = http_error_log_content(e.response)
        logger.error(
            "HTTP request failed (%d %s): %s",
            e.response.status_code,
            e.response.reason_phrase,
            content,
            exc_info=True,
        )
        raise e
    except httpx.RequestError as e:
        logger.error(f"Request failed: {e}", exc_info=DEBUG)
        raise e


class WebSocketCallbackClient:
    """Minimal WS client: connects, forwards events, retries on error."""

    host: str
    conversation_id: str
    callback: ConversationCallbackType
    on_reconnect: Callable[[], object] | None
    api_key: str | None
    _thread: threading.Thread | None
    _stop: threading.Event
    _ready: threading.Event

    def __init__(
        self,
        host: str,
        conversation_id: str,
        callback: ConversationCallbackType,
        api_key: str | None = None,
        on_reconnect: Callable[[], object] | None = None,
    ):
        self.host = host
        self.conversation_id = conversation_id
        self.callback = callback
        self.api_key = api_key
        self.on_reconnect = on_reconnect
        self._thread = None
        self._stop = threading.Event()
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._thread:
            return
        self._stop.set()
        self._thread.join(timeout=5)
        self._thread = None

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """Wait for WebSocket subscription to complete.

        The server sends a ConversationStateUpdateEvent immediately after
        subscription completes. This method blocks until that event is received,
        the client is stopped, or the timeout expires.

        Args:
            timeout: Maximum time to wait in seconds. None means wait forever.

        Returns:
            True if the WebSocket is ready, False if stopped or timeout expired.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # Calculate remaining timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait_timeout = min(0.05, remaining)
            else:
                wait_timeout = 0.05

            # Wait efficiently using Event.wait() instead of sleep
            if self._ready.wait(timeout=wait_timeout):
                return True

            # Check if stopped
            if self._stop.is_set():
                return False

    def _run(self) -> None:
        try:
            asyncio.run(self._client_loop())
        except RuntimeError:
            # Fallback in case of an already running loop in rare environments
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._client_loop())
            loop.close()

    async def _client_loop(self) -> None:
        parsed = urlparse(self.host)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        base = f"{ws_scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
        ws_url = f"{base}/sockets/events/{self.conversation_id}"

        # Add API key as query parameter if provided
        if self.api_key:
            ws_url += f"?session_api_key={quote(self.api_key, safe='')}"

        delay = 1.0
        has_connected = False
        while not self._stop.is_set():
            try:
                async with websockets.connect(ws_url) as ws:
                    delay = 1.0
                    connection_ready = False
                    async for message in ws:
                        if self._stop.is_set():
                            break
                        try:
                            event = Event.model_validate(json.loads(message))

                            # Set ready on first ConversationStateUpdateEvent
                            # The server sends this immediately after subscription
                            if (
                                isinstance(event, ConversationStateUpdateEvent)
                                and not connection_ready
                            ):
                                connection_ready = True
                                if has_connected:
                                    await self._handle_reconnect()
                                else:
                                    has_connected = True
                                    self._ready.set()

                            self.callback(event)
                        except Exception:
                            logger.exception(
                                "ws_event_processing_error", stack_info=True
                            )
            except ConnectionClosed as exc:
                if _is_fatal_websocket_close(exc):
                    logger.debug("ws_connection_closed_fatal", exc_info=True)
                    self._stop.set()
                    break
                logger.debug("ws_connection_closed_retry", exc_info=True)
                await self._sleep_before_retry(delay)
                delay = min(delay * 2, 30.0)
            except Exception:
                logger.debug("ws_connect_retry", exc_info=True)
                await self._sleep_before_retry(delay)
                delay = min(delay * 2, 30.0)

    async def _handle_reconnect(self) -> None:
        if not self.on_reconnect:
            return
        try:
            await asyncio.to_thread(self.on_reconnect)
        except Exception:
            logger.exception("ws_reconnect_callback_error", stack_info=True)

    async def _sleep_before_retry(self, delay: float) -> None:
        deadline = time.monotonic() + delay
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.1, remaining))


class RemoteEventsList(EventsListBase):
    """A list-like, read-only view of remote conversation events.

    On first access it fetches existing events from the server. Afterwards,
    it relies on the WebSocket stream to incrementally append new events.
    """

    _client: httpx.Client
    _conversation_id: str
    _events_base_path: str
    _cached_events: list[Event]
    _cached_event_ids: set[str]
    _lock: threading.RLock

    def __init__(
        self,
        client: httpx.Client,
        conversation_id: str,
        events_base_path: str = LEGACY_CONVERSATIONS_PATH,
    ):
        self._client = client
        self._conversation_id = conversation_id
        self._events_base_path = events_base_path
        self._cached_events: list[Event] = []
        self._cached_event_ids: set[str] = set()
        self._acp_tool_call_id_to_event_id: dict[str, str] = {}
        self._lock = threading.RLock()
        # Initial fetch to sync existing events
        self._do_full_sync()

    def _do_full_sync(self) -> None:
        """Perform a full sync with the remote API."""
        logger.debug(f"Performing full sync for conversation {self._conversation_id}")

        events = []
        page_id = None

        while True:
            params = {"limit": 100}
            if page_id:
                params["page_id"] = page_id

            resp = _send_request(
                self._client,
                "GET",
                f"{self._events_base_path}/{self._conversation_id}/events/search",
                params=params,
            )
            data = resp.json()

            events.extend([Event.model_validate(item) for item in data["items"]])

            if not data.get("next_page_id"):
                break
            page_id = data["next_page_id"]

        self._cached_events = events
        self._cached_event_ids.update(e.id for e in events)
        logger.debug(f"Full sync completed, {len(events)} events cached")

    def reconcile(self) -> int:
        """Reconcile local cache with server by fetching and merging events.

        This method fetches all events from the server and merges them with
        the local cache, deduplicating by event ID. This ensures no events
        are missed due to race conditions between REST sync and WebSocket
        subscription.

        Returns:
            Number of new events added during reconciliation.
        """
        logger.debug(
            f"Performing reconciliation sync for conversation {self._conversation_id}"
        )

        events = []
        page_id = None

        while True:
            params = {"limit": 100}
            if page_id:
                params["page_id"] = page_id

            try:
                resp = _send_request(
                    self._client,
                    "GET",
                    f"{self._events_base_path}/{self._conversation_id}/events/search",
                    params=params,
                )
                data = resp.json()
            except Exception as e:
                logger.warning(f"Failed to fetch events during reconciliation: {e}")
                break  # Return partial results rather than failing completely

            events.extend([Event.model_validate(item) for item in data["items"]])

            if not data.get("next_page_id"):
                break
            page_id = data["next_page_id"]

        # Merge events into cache, acquiring lock once for all events
        added_count = 0
        with self._lock:
            for event in events:
                if event.id not in self._cached_event_ids:
                    self._add_event_unsafe(event)
                    added_count += 1

        logger.debug(
            f"Reconciliation completed, {added_count} new events added "
            f"(total: {len(self._cached_events)})"
        )
        return added_count

    def _add_event_unsafe(self, event: Event) -> None:
        """Add event to cache without acquiring lock (caller must hold lock)."""
        # ACP emits two ACPToolCallEvents per call — an early ``started`` event
        # and one terminal (``completed`` / ``failed``) event — the
        # action->observation pair for a tool call. Merge by tool_call_id:
        # replace the ``started`` entry in-place with the terminal one so a
        # single card updates from running to its result, mirroring how an
        # ObservationEvent supersedes its ActionEvent. (The source no longer
        # fans out one frame per cumulative-output ToolCallProgress, so this is
        # an O(1) two-event merge, not an O(n²) dedup.)
        if isinstance(event, ACPToolCallEvent):
            existing_id = self._acp_tool_call_id_to_event_id.get(event.tool_call_id)
            if existing_id is not None:
                for i, e in enumerate(self._cached_events):
                    if e.id == existing_id:
                        self._cached_events[i] = event
                        self._cached_event_ids.discard(existing_id)
                        self._cached_event_ids.add(event.id)
                        self._acp_tool_call_id_to_event_id[event.tool_call_id] = (
                            event.id
                        )
                        logger.debug(
                            f"Replaced ACP tool call event {existing_id} -> {event.id} "
                            f"(tool_call_id={event.tool_call_id})"
                        )
                        return
                # Index pointed to an event that is no longer in _cached_events;
                # clean up the stale entry so we don't carry it forward.
                logger.warning(
                    "Stale ACP tool-call index entry: "
                    f"tool_call_id={event.tool_call_id} "
                    f"pointed to event {existing_id} "
                    "not found in _cached_events; removing stale entry."
                )
                self._cached_event_ids.discard(existing_id)
                del self._acp_tool_call_id_to_event_id[event.tool_call_id]

        # Use bisect with key function for O(log N) insertion
        # This ensures events are always ordered correctly even if
        # WebSocket delivers them out of order
        insert_pos = bisect.bisect_right(
            self._cached_events, event.timestamp, key=lambda e: e.timestamp
        )
        self._cached_events.insert(insert_pos, event)
        self._cached_event_ids.add(event.id)
        if isinstance(event, ACPToolCallEvent):
            self._acp_tool_call_id_to_event_id[event.tool_call_id] = event.id
        logger.debug(f"Added event {event.id} to local cache at position {insert_pos}")

    def add_event(self, event: Event) -> None:
        """Add a new event to the local cache (called by WebSocket callback).

        Events are inserted in sorted order by timestamp to maintain correct
        temporal ordering regardless of WebSocket delivery order.
        """
        with self._lock:
            # Check if event already exists to avoid duplicates
            if event.id not in self._cached_event_ids:
                self._add_event_unsafe(event)

    def append(self, event: Event) -> None:
        """Add a new event to the list (for compatibility with EventLog interface)."""
        self.add_event(event)

    def create_default_callback(self) -> ConversationCallbackType:
        """Create a default callback that adds events to this list."""

        def callback(event: Event) -> None:
            self.add_event(event)

        return callback

    def __len__(self) -> int:
        return len(self._cached_events)

    @overload
    def __getitem__(self, index: int) -> Event: ...

    @overload
    def __getitem__(self, index: slice) -> list[Event]: ...

    def __getitem__(self, index: SupportsIndex | slice) -> Event | list[Event]:
        with self._lock:
            return self._cached_events[index]

    def __iter__(self):
        with self._lock:
            return iter(self._cached_events)


class RemoteState(ConversationStateProtocol):
    """A state-like interface for accessing remote conversation state."""

    _client: httpx.Client
    _conversation_id: str
    _conversation_info_base_path: str
    _events: RemoteEventsList
    _cached_state: dict | None
    _lock: threading.RLock

    def __init__(
        self,
        client: httpx.Client,
        conversation_id: str,
        conversation_info_base_path: str = LEGACY_CONVERSATIONS_PATH,
        events_base_path: str = LEGACY_CONVERSATIONS_PATH,
    ):
        self._client = client
        self._conversation_id = conversation_id
        self._conversation_info_base_path = conversation_info_base_path
        self._events = RemoteEventsList(client, conversation_id, events_base_path)

        # Cache for state information to avoid REST calls
        self._cached_state = None
        self._lock = threading.RLock()

    def _get_conversation_info(self) -> dict:
        """Fetch the latest conversation info from the remote API."""
        with self._lock:
            # Return cached state if available
            if self._cached_state is not None:
                return self._cached_state

            # Fallback to REST API if no cached state
            return self.refresh_from_server()

    def refresh_from_server(self) -> dict:
        """Fetch and cache the latest authoritative conversation state."""
        resp = _send_request(
            self._client,
            "GET",
            f"{self._conversation_info_base_path}/{self._conversation_id}",
        )
        state = resp.json()
        with self._lock:
            self._cached_state = state
            return state

    def update_state_from_event(self, event: ConversationStateUpdateEvent) -> None:
        """Update cached state from a ConversationStateUpdateEvent."""
        with self._lock:
            # Handle full state snapshot
            if event.key == FULL_STATE_KEY:
                # Update cached state with the full snapshot
                if self._cached_state is None:
                    self._cached_state = {}
                self._cached_state.update(event.value)
            else:
                # Handle individual field updates
                if self._cached_state is None:
                    self._cached_state = {}
                self._cached_state[event.key] = event.value

    def create_state_update_callback(self) -> ConversationCallbackType:
        """Create a callback that updates state from ConversationStateUpdateEvent."""

        def callback(event: Event) -> None:
            if isinstance(event, ConversationStateUpdateEvent):
                self.update_state_from_event(event)

        return callback

    @property
    def events(self) -> RemoteEventsList:
        """Access to the events list."""
        return self._events

    @property
    def id(self) -> ConversationID:
        """The conversation ID."""
        return uuid.UUID(self._conversation_id)

    @property
    def execution_status(self) -> ConversationExecutionStatus:
        """The current conversation execution status."""
        info = self._get_conversation_info()
        status_str = info.get("execution_status")
        if status_str is None:
            raise RuntimeError(
                "execution_status missing in conversation info: " + str(info)
            )
        return ConversationExecutionStatus(status_str)

    @execution_status.setter
    def execution_status(self, value: ConversationExecutionStatus) -> None:
        """Set execution status is No-OP for RemoteConversation.

        # For remote conversations, execution status is managed server-side
        # This setter is provided for test compatibility but doesn't actually change remote state  # noqa: E501
        """  # noqa: E501
        raise NotImplementedError(
            f"Setting execution_status on RemoteState has no effect. "
            f"Remote execution status is managed server-side. Attempted to set: {value}"
        )

    @property
    def confirmation_policy(self) -> ConfirmationPolicyBase:
        """The confirmation policy."""
        info = self._get_conversation_info()
        policy_data = info.get("confirmation_policy")
        if policy_data is None:
            raise RuntimeError(
                "confirmation_policy missing in conversation info: " + str(info)
            )
        return ConfirmationPolicyBase.model_validate(policy_data)

    @property
    def security_analyzer(self) -> SecurityAnalyzerBase | None:
        """The security analyzer."""
        info = self._get_conversation_info()
        analyzer_data = info.get("security_analyzer")
        if analyzer_data:
            return SecurityAnalyzerBase.model_validate(analyzer_data)

        return None

    @property
    def activated_knowledge_skills(self) -> list[str]:
        """List of activated knowledge skills."""
        info = self._get_conversation_info()
        return info.get("activated_knowledge_skills", [])

    @property
    def invoked_skills(self) -> list[str]:
        """Names of progressive-disclosure skills explicitly invoked."""
        info = self._get_conversation_info()
        return info.get("invoked_skills", [])

    @property
    def agent(self):
        """The agent configuration (fetched from remote)."""
        info = self._get_conversation_info()
        agent_data = info.get("agent")
        if agent_data is None:
            raise RuntimeError("agent missing in conversation info: " + str(info))
        return _validate_remote_agent(agent_data)

    @property
    def workspace(self):
        """The working directory (fetched from remote)."""
        info = self._get_conversation_info()
        workspace = info.get("workspace")
        if workspace is None:
            raise RuntimeError("workspace missing in conversation info: " + str(info))
        return workspace

    @property
    def persistence_dir(self):
        """The persistence directory (fetched from remote)."""
        info = self._get_conversation_info()
        persistence_dir = info.get("persistence_dir")
        if persistence_dir is None:
            raise RuntimeError(
                "persistence_dir missing in conversation info: " + str(info)
            )
        return persistence_dir

    @property
    def stats(self) -> ConversationStats:
        """Get conversation stats (fetched from remote)."""
        info = self._get_conversation_info()
        stats_data = info.get("stats", {})
        return ConversationStats.model_validate(stats_data)

    @property
    def hook_config(self) -> HookConfig | None:
        """Get hook configuration (fetched from remote)."""
        info = self._get_conversation_info()
        hook_config_data = info.get("hook_config")
        if hook_config_data is not None:
            return HookConfig.model_validate(hook_config_data)
        return None

    def model_dump(self, **_kwargs):
        """Get a dictionary representation of the remote state."""
        info = self._get_conversation_info()
        return info

    def model_dump_json(self, **kwargs):
        """Get a JSON representation of the remote state."""
        return json.dumps(self.model_dump(**kwargs))

    # Context manager methods for compatibility with ConversationState
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class RemoteConversation(BaseConversation):
    _id: uuid.UUID
    _state: "RemoteState"
    _visualizer: ConversationVisualizerBase | None
    _ws_client: "WebSocketCallbackClient | None"
    agent: AgentBase
    _callbacks: list[ConversationCallbackType]
    max_iteration_per_run: int
    workspace: RemoteWorkspace
    _client: httpx.Client
    _cleanup_initiated: bool
    _terminal_status_queue: Queue[str]
    _run_armed: threading.Event
    _conversation_info_base_path: str
    _conversation_action_base_path: str
    delete_on_close: bool = False

    def __init__(
        self,
        agent: AgentBase,
        workspace: RemoteWorkspace,
        plugins: list | None = None,
        conversation_id: ConversationID | None = None,
        callbacks: list[ConversationCallbackType] | None = None,
        max_iteration_per_run: int = 500,
        stuck_detection: bool = True,
        stuck_detection_thresholds: (
            StuckDetectionThresholds | Mapping[str, int] | None
        ) = None,
        hook_config: HookConfig | None = None,
        visualizer: (
            type[ConversationVisualizerBase] | ConversationVisualizerBase | None
        ) = DefaultConversationVisualizer,
        secrets: Mapping[str, SecretValue] | None = None,
        delete_on_close: bool = False,
        tags: dict[str, str] | None = None,
        user_id: str | None = None,
        client_tools: list[ClientToolSpec] | None = None,
        observability_metadata: dict[str, TraceMetadataValue] | None = None,
        observability_tags: list[str] | None = None,
        observability_span_name: str = "conversation",
        **_: object,
    ) -> None:
        """Remote conversation proxy that talks to an agent server.

        Args:
            agent: Agent configuration (will be sent to the server)
            workspace: The working directory for agent operations and tool execution.
            plugins: Optional list of plugins to load on the server. Each plugin
                    is a PluginSource specifying source, ref, and repo_path.
            conversation_id: Optional existing conversation id to attach to
            callbacks: Optional callbacks to receive events (not yet streamed)
            max_iteration_per_run: Max iterations configured on server
            stuck_detection: Whether to enable stuck detection on server
            stuck_detection_thresholds: Optional configuration for stuck detection
                      thresholds. Can be a StuckDetectionThresholds instance or
                      a dict with keys: 'action_observation', 'action_error',
                      'monologue', 'alternating_pattern'. Values are integers
                      representing the number of repetitions before triggering.
            hook_config: Optional hook configuration sent to the server.
                      All hooks are executed server-side.
            visualizer: Visualization configuration. Can be:
                       - ConversationVisualizerBase subclass: Class to instantiate
                         (default: ConversationVisualizer)
                       - ConversationVisualizerBase instance: Use custom visualizer
                       - None: No visualization
            secrets: Optional secrets to initialize the conversation with
            tags: Optional key-value tags for the conversation. Keys must be
                  lowercase alphanumeric, values up to 256 characters.
            user_id: Optional user ID to associate with observability traces
            client_tools: Optional list of client-defined tool specs. These tools
                      have no server-side executor — when the agent calls them an
                      ActionEvent is emitted over the WebSocket and the client
                      handles execution via callbacks.
            observability_metadata: Optional trace metadata for observability backends.
            observability_tags: Optional root span tags for observability backends.
            observability_span_name: Optional child span name for observability
                      backends. The root span remains named "conversation".
        """
        super().__init__()  # Initialize base class with span tracking
        self.agent = agent
        self._callbacks = callbacks or []
        self.max_iteration_per_run = max_iteration_per_run
        self.workspace = workspace
        self._client = workspace.client
        self._conversation_info_base_path = LEGACY_CONVERSATIONS_PATH
        self._conversation_action_base_path = LEGACY_CONVERSATIONS_PATH
        self._cleanup_initiated = False
        self._terminal_status_queue: Queue[str] = Queue()
        self._run_armed = threading.Event()

        # Client tool specs the server already has persisted for this
        # conversation (populated when re-attaching to an existing one). These
        # must be registered locally before the initial event sync so that
        # persisted ``ClientAction_*`` events can be deserialized.
        attached_client_tools: list[ClientToolSpec] = []

        should_create = conversation_id is None
        if conversation_id is not None:
            # Try to attach to existing conversation
            resp = _send_request(
                self._client,
                "GET",
                f"{self._conversation_info_base_path}/{conversation_id}",
                acceptable_status_codes={404},
            )
            if resp.status_code == 404:
                # Conversation doesn't exist, we'll create it
                should_create = True
            else:
                info = resp.json()
                agent_payload = info.get("agent")
                if agent_payload is not None:
                    remote_agent = _validate_remote_agent(agent_payload)
                    if remote_agent.agent_kind != agent.agent_kind:
                        raise ValueError(_agent_kind_mismatch_message(conversation_id))
                # Capture persisted client tool specs so we can register their
                # dynamic action types before RemoteState syncs events.
                for raw_spec in info.get("client_tools") or []:
                    attached_client_tools.append(
                        ClientToolSpec.model_validate(raw_spec)
                    )
                # Conversation exists, use the provided ID
                self._id = conversation_id

        if should_create:
            # Import here to avoid circular imports
            from openhands.sdk.subagent.registry import get_registered_agent_definitions
            from openhands.sdk.tool.registry import get_tool_module_qualnames

            tool_qualnames = get_tool_module_qualnames()
            logger.debug(f"Sending tool_module_qualnames to server: {tool_qualnames}")

            agent_defs = get_registered_agent_definitions()
            serialized_defs = [d.model_dump(mode="json") for d in agent_defs]
            logger.debug(f"Sending {len(serialized_defs)} agent_definitions to server")

            payload = {
                "agent": agent.model_dump(
                    mode="json", context={"expose_secrets": True}
                ),
                "initial_message": None,
                "max_iterations": max_iteration_per_run,
                "stuck_detection": stuck_detection,
                # We need to convert RemoteWorkspace to LocalWorkspace for the server
                "workspace": LocalWorkspace(
                    working_dir=self.workspace.working_dir
                ).model_dump(),
                # Include tool module qualnames for dynamic registration on server
                "tool_module_qualnames": tool_qualnames,
                # Include agent definitions for subagent registration on server
                "agent_definitions": serialized_defs,
                # Include plugins to load on server
                "plugins": [p.model_dump() for p in plugins] if plugins else None,
                # Include hook_config for server-side hooks
                "hook_config": hook_config.model_dump() if hook_config else None,
                # Include client-defined tool specs (no server-side executor)
                "client_tools": (
                    [s.model_dump(mode="json") for s in client_tools]
                    if client_tools
                    else []
                ),
                # Include tags and observability metadata if provided
                "tags": tags if tags is not None else {},
                "observability_metadata": observability_metadata
                if observability_metadata is not None
                else {},
                "observability_tags": observability_tags
                if observability_tags is not None
                else [],
                "observability_span_name": observability_span_name,
                "user_id": user_id,
            }
            if user_id:
                payload["user_id"] = user_id
            if stuck_detection_thresholds is not None:
                # Convert to StuckDetectionThresholds if dict, then serialize
                if isinstance(stuck_detection_thresholds, Mapping):
                    threshold_config = StuckDetectionThresholds(
                        **stuck_detection_thresholds
                    )
                else:
                    threshold_config = stuck_detection_thresholds
                payload["stuck_detection_thresholds"] = threshold_config.model_dump()
            # Include conversation_id if provided (for creating with specific ID)
            if conversation_id is not None:
                payload["conversation_id"] = str(conversation_id)
            resp = _send_request(
                self._client,
                "POST",
                self._conversation_info_base_path,
                json=payload,
            )
            data = resp.json()
            # Expect a ConversationInfo
            cid = data.get("id") or data.get("conversation_id")
            if not cid:
                raise RuntimeError(
                    "Invalid response from server: missing conversation id"
                )
            self._id = uuid.UUID(cid)

            workspace.register_conversation(str(self._id))

        # Register client tool action types locally so WebSocket/persisted
        # events with ClientAction_* action_type can be deserialized by the
        # event loop. This must cover both the specs the caller passed in and
        # the specs the server already had persisted (when re-attaching), so a
        # plain reattach by conversation_id can still sync persisted events.
        seen_client_tool_names: set[str] = set()
        for spec in [*(client_tools or []), *attached_client_tools]:
            if spec.name in seen_client_tool_names:
                continue
            seen_client_tool_names.add(spec.name)
            ClientTool.from_spec(spec)

        # Initialize the remote state
        self._state = RemoteState(
            self._client,
            str(self._id),
            conversation_info_base_path=self._conversation_info_base_path,
            events_base_path=self._conversation_action_base_path,
        )

        # Add default callback to maintain local event state
        default_callback = self._state.events.create_default_callback()
        self._callbacks.append(default_callback)

        # Add callback to update state from websocket events
        state_update_callback = self._state.create_state_update_callback()
        self._callbacks.append(state_update_callback)

        # Add callback to handle LLM completion logs
        # Register callback if any LLM has log_completions enabled
        if any(llm.log_completions for llm in agent.get_all_llms()):
            llm_log_callback = self._create_llm_completion_log_callback()
            self._callbacks.append(llm_log_callback)

        # Handle visualization configuration
        if isinstance(visualizer, ConversationVisualizerBase):
            # Use custom visualizer instance
            self._visualizer = visualizer
            # Initialize the visualizer with conversation state
            self._visualizer.initialize(self._state)
            self._callbacks.append(self._visualizer.on_event)
        elif isinstance(visualizer, type) and issubclass(
            visualizer, ConversationVisualizerBase
        ):
            # Instantiate the visualizer class with appropriate parameters
            self._visualizer = visualizer()
            # Initialize with state
            self._visualizer.initialize(self._state)
            self._callbacks.append(self._visualizer.on_event)
        else:
            # No visualization (visualizer is None)
            self._visualizer = None

        # Add a callback that signals when run completes via WebSocket.
        # The server's post-run full-state snapshot is the only authoritative
        # WebSocket success signal. Per-field FINISHED is a hint because stop
        # hooks can still revert it; per-field ERROR/STUCK remain immediate.
        def run_complete_callback(event: Event) -> None:
            if not isinstance(event, ConversationStateUpdateEvent):
                return

            if event.key == "execution_status":
                try:
                    status = ConversationExecutionStatus(event.value)
                except ValueError:
                    return
                if status in (
                    ConversationExecutionStatus.ERROR,
                    ConversationExecutionStatus.STUCK,
                ):
                    self._terminal_status_queue.put(status.value)
                return

            if event.key != FULL_STATE_KEY:
                return

            # Only accept full-state snapshots as run-completion signals when a
            # run is actually in progress. The WS subscription delivers an
            # initial full-state snapshot during connect(); if that snapshot
            # carries a non-RUNNING status (e.g. "idle"), it could be picked up
            # by _wait_for_run_completion() as the completion signal for the
            # *next* run() invocation, causing blocking=True to return before the
            # server has actually finished.
            if not self._run_armed.is_set():
                return

            raw_status = event.value.get("execution_status")
            try:
                status = ConversationExecutionStatus(raw_status)
            except ValueError:
                return

            if status != ConversationExecutionStatus.RUNNING:
                self._terminal_status_queue.put(status.value)

        # Compose all callbacks into a single callback
        all_callbacks = self._callbacks + [run_complete_callback]
        composed_callback = BaseConversation.compose_callbacks(all_callbacks)

        # Initialize WebSocket client for callbacks
        self._ws_client = WebSocketCallbackClient(
            host=self.workspace.host,
            conversation_id=str(self._id),
            callback=composed_callback,
            api_key=self.workspace.api_key,
            on_reconnect=self._state.events.reconcile,
        )
        self._ws_client.start()

        # Wait for WebSocket subscription to complete before allowing operations.
        # This ensures events emitted during send_message() are not missed.
        # The server sends a ConversationStateUpdateEvent after subscription.
        ws_timeout = float(os.getenv("OPENHANDS_REMOTE_WS_READY_TIMEOUT", "30"))
        if not self._ws_client.wait_until_ready(timeout=ws_timeout):
            if os.getenv("OPENHANDS_REMOTE_WS_READY_REQUIRED", "true").lower() in (
                "0",
                "false",
                "no",
            ):
                logger.warning(
                    "WebSocket subscription did not become ready within %.1f "
                    "seconds for conversation %s; continuing after REST "
                    "reconciliation because OPENHANDS_REMOTE_WS_READY_REQUIRED "
                    "is false.",
                    ws_timeout,
                    self._id,
                )
            else:
                try:
                    self._ws_client.stop()
                except Exception:
                    pass
                finally:
                    self._ws_client = None
                raise WebSocketConnectionError(
                    conversation_id=self._id,
                    timeout=ws_timeout,
                )

        # Reconcile events after WebSocket is ready to catch any events that
        # were emitted between the initial REST sync and WebSocket subscription.
        # This is the "reconciliation" part of the subscription handshake.
        self._state.events.reconcile()

        # Initialize secrets if provided
        if secrets:
            # Convert dict[str, str] to dict[str, SecretValue]
            secret_values: dict[str, SecretValue] = {k: v for k, v in secrets.items()}
            self.update_secrets(secret_values)

        self._start_observability_span(
            str(self._id),
            span_name=observability_span_name,
            user_id=user_id,
            metadata=observability_metadata,
            tags=observability_tags,
            conversation_tags=tags,
        )
        # All hooks (including SessionStart/SessionEnd) are executed server-side.
        # hook_config is sent in the creation payload.
        self.delete_on_close = delete_on_close

    def _create_llm_completion_log_callback(self) -> ConversationCallbackType:
        """Create a callback that writes LLM completion logs to client filesystem."""

        def callback(event: Event) -> None:
            if not isinstance(event, LLMCompletionLogEvent):
                return

            # Find the LLM with matching usage_id
            target_llm = None
            for llm in self.agent.get_all_llms():
                if llm.usage_id == event.usage_id:
                    target_llm = llm
                    break

            if not target_llm or not target_llm.log_completions:
                logger.debug(
                    f"No LLM with log_completions enabled found "
                    f"for usage_id={event.usage_id}"
                )
                return

            try:
                log_dir = target_llm.log_completions_folder
                os.makedirs(log_dir, exist_ok=True)
                log_path = os.path.join(log_dir, event.filename)
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(event.log_data)
                logger.debug(f"Wrote LLM completion log to {log_path}")
            except Exception as e:
                logger.warning(f"Failed to write LLM completion log: {e}")

        return callback

    @property
    def id(self) -> ConversationID:
        return self._id

    @property
    def state(self) -> RemoteState:
        """Access to remote conversation state."""
        return self._state

    @property
    def conversation_stats(self):
        return self._state.stats

    @property
    def stuck_detector(self):
        """Stuck detector for compatibility.
        Not implemented for remote conversations."""
        raise NotImplementedError(
            "For remote conversations, stuck detection is not available"
            " since it would be handled server-side."
        )

    @observe(name="conversation.send_message")
    def send_message(self, message: str | Message, sender: str | None = None) -> None:
        if isinstance(message, str):
            message = Message(role="user", content=[TextContent(text=message)])
        assert message.role == "user", (
            "Only user messages are allowed to be sent to the agent."
        )
        payload = {
            "role": message.role,
            "content": [c.model_dump() for c in message.content],
            "run": False,  # Mirror local semantics; explicit run() must be called
        }
        if sender is not None:
            payload["sender"] = sender
        _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/events",
            json=payload,
        )

    @observe(name="conversation.run")
    def run(
        self,
        blocking: bool = True,
        poll_interval: float = 1.0,
        timeout: float = 3600.0,
    ) -> None:
        """Trigger a run on the server.

        Args:
            blocking: If True (default), wait for the run to complete by polling
                the server. If False, return immediately after triggering the run.
            poll_interval: Time in seconds between status polls (only used when
                blocking=True). Default is 1.0 second.
            timeout: Maximum time in seconds to wait for the run to complete
                (only used when blocking=True). Default is 3600 seconds.

        Raises:
            ConversationRunError: If the run fails or times out.
        """
        # Disarm and drain any stale terminal status events from previous runs
        # before arming for the new one. _run_armed gates full-state WS snapshots
        # in run_complete_callback; clearing it here prevents the initial WS
        # subscription snapshot from being mistaken for the post-run snapshot.
        self._run_armed.clear()
        self._drain_terminal_status_queue()

        # Trigger a run on the server using the dedicated run endpoint.
        # Let the server tell us if it's already running (409), avoiding an extra GET.
        try:
            resp = _send_request(
                self._client,
                "POST",
                f"{self._conversation_action_base_path}/{self._id}/run",
                acceptable_status_codes={200, 201, 204, 409},
                timeout=30,  # Short timeout for trigger request
            )
        except Exception as e:  # httpx errors already logged by _send_request
            # Surface conversation id to help resuming
            raise ConversationRunError(self._id, e) from e

        if resp.status_code == 409:
            logger.info("Conversation is already running; skipping run trigger")
        else:
            logger.info(f"run() triggered successfully: {resp}")

        if blocking:
            # Arm after POST so that only WS full-state snapshots arriving
            # after the run was triggered are treated as run-completion signals.
            self._run_armed.set()
            try:
                self._wait_for_run_completion(poll_interval, timeout)
            finally:
                self._run_armed.clear()

    def _wait_for_run_completion(
        self,
        poll_interval: float = 1.0,
        timeout: float = 1800.0,
    ) -> None:
        """Wait for the conversation run to complete.

        This method waits for the run to complete by listening for the terminal
        status event via WebSocket. This ensures all events are delivered before
        returning, avoiding the race condition where polling sees "finished"
        status before WebSocket delivers the final events.

        As a fallback, it also polls the server periodically. If the WebSocket
        is delayed or disconnected, polling still surfaces ERROR/STUCK promptly.
        A REST-only FINISHED status is not authoritative because stop hooks can
        still revert it to RUNNING before the server-side run task exits.

        Args:
            poll_interval: Time in seconds between status polls (fallback).
            timeout: Maximum time in seconds to wait.

        Raises:
            ConversationRunError: If the run fails, the conversation disappears,
                or the wait times out. Transient network errors, 429s, and 5xx
                responses are retried until timeout.
        """
        start_time = time.monotonic()
        terminal_poll_count = 0
        # Log a warning after this many consecutive REST terminal polls without a
        # post-run WS snapshot. This is a health signal, not a return path —
        # returning immediately on REST FINISHED would reintroduce the stop-hook race.
        TERMINAL_POLL_WARNING_THRESHOLD = 3
        # Time-based hard fallback: accept REST-confirmed terminal status after
        # the server has been reporting terminal for at least this many seconds
        # without a post-run WS snapshot. Stop hooks are fast (seconds); 30 s
        # is a safe bound regardless of poll_interval. This prevents an
        # indefinite hang when the WS snapshot is never delivered (e.g., socket
        # dropped after the run finishes on the server).
        TERMINAL_HARD_FALLBACK_SECS = 30.0
        terminal_first_seen_at: float | None = None

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > timeout:
                raise ConversationRunError(
                    self._id,
                    TimeoutError(
                        f"Run timed out after {timeout} seconds. "
                        "The conversation may still be running on the server."
                    ),
                )

            # Wait for either:
            # 1. WebSocket delivers a run-completion signal
            # 2. Poll interval expires (fall through to REST poll)
            try:
                ws_status = self._terminal_status_queue.get(timeout=poll_interval)
                # Raises ConversationRunError on ERROR/STUCK; no-op otherwise.
                self._handle_conversation_status(ws_status)
                logger.info(
                    "Run completed via post-run WebSocket state update "
                    "(status: %s, elapsed: %.1fs)",
                    ws_status,
                    elapsed,
                )
                # The server publishes the full ConversationStateUpdateEvent after
                # conversation.run()/arun() exits and pending events are flushed,
                # so non-running statuses from that snapshot are authoritative
                # run-complete signals.
                self._state.events.reconcile()
                return
            except Empty:
                pass  # Queue.get() timed out, fall through to REST polling

            # Poll the server for status as a health check and fallback.
            # This catches ERROR/STUCK states that need immediate attention,
            # and provides a fallback if WebSocket is delayed/disconnected.
            try:
                status = self._poll_status_once()
            except Exception as exc:
                self._handle_poll_exception(exc)
                # Reset on error: we cannot confirm the server is still in a
                # terminal state after a failed poll, so conservatively restart
                # the hard-fallback timer. In the degenerate case where polls
                # alternate between terminal and exception, the 30 s threshold
                # slides; this is intentional — we prefer a false-negative wait
                # over a false-positive early return.
                terminal_poll_count = 0
                terminal_first_seen_at = None
            else:
                # Raises ConversationRunError for ERROR/STUCK states
                self._handle_conversation_status(status)

                if status and ConversationExecutionStatus(status).is_terminal():
                    # ERROR/STUCK have already been handled above. FINISHED from
                    # REST is advisory because stop hooks can still veto it;
                    # prefer waiting for the server's post-run WebSocket state update.
                    terminal_poll_count += 1
                    now = time.monotonic()
                    if terminal_first_seen_at is None:
                        terminal_first_seen_at = now
                    if terminal_poll_count == TERMINAL_POLL_WARNING_THRESHOLD:
                        logger.warning(
                            "REST has reported terminal status %s for %d polls "
                            "without a post-run WebSocket snapshot; continuing "
                            "to wait for the authoritative snapshot "
                            "(elapsed: %.1fs)",
                            status,
                            terminal_poll_count,
                            elapsed,
                        )
                    terminal_secs = now - terminal_first_seen_at
                    if terminal_secs >= TERMINAL_HARD_FALLBACK_SECS:
                        logger.warning(
                            "REST has reported terminal status %s for %.0fs "
                            "with no post-run WebSocket snapshot; accepting as "
                            "final to avoid an indefinite hang (elapsed: %.1fs). "
                            "This may indicate a WebSocket delivery issue.",
                            status,
                            terminal_secs,
                            elapsed,
                        )
                        self._state.refresh_from_server()
                        self._state.events.reconcile()
                        return
                else:
                    terminal_poll_count = 0
                    terminal_first_seen_at = None

    def _drain_terminal_status_queue(self) -> None:
        """Empty the WS terminal-status hint queue.

        Called at the start of run() (before arming) to discard any stale
        signals left over from a previous run invocation.
        """
        while True:
            try:
                self._terminal_status_queue.get_nowait()
            except Empty:
                break

    def _poll_status_once(self) -> str | None:
        """Fetch the current execution status from the remote conversation."""
        resp = _send_request(
            self._client,
            "GET",
            f"{self._conversation_info_base_path}/{self._id}",
            timeout=30,
        )
        info = resp.json()
        return info.get("execution_status")

    def _handle_conversation_status(self, status: str | None) -> bool:
        """Handle non-running statuses; return True if the run is complete."""
        if status == ConversationExecutionStatus.RUNNING.value:
            return False
        if status == ConversationExecutionStatus.ERROR.value:
            detail = self._get_last_error_detail()
            raise ConversationRunError(
                self._id,
                RuntimeError(detail or "Remote conversation ended with error"),
            )
        if status == ConversationExecutionStatus.STUCK.value:
            raise ConversationRunError(
                self._id,
                RuntimeError("Remote conversation got stuck"),
            )
        return True

    def _handle_poll_exception(self, exc: Exception) -> None:
        """Classify polling exceptions into retryable vs terminal failures."""
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            reason = exc.response.reason_phrase
            if status_code == 404:
                raise ConversationRunError(
                    self._id,
                    RuntimeError(
                        "Remote conversation not found (404). "
                        "The runtime may have been deleted."
                    ),
                ) from exc
            if 400 <= status_code < 500 and status_code != 429:
                raise ConversationRunError(
                    self._id,
                    RuntimeError(f"Polling failed with HTTP {status_code} {reason}"),
                ) from exc
            logger.warning(
                "Error polling status (will retry): HTTP %d %s",
                status_code,
                reason,
            )
            return
        if isinstance(exc, httpx.RequestError):
            logger.warning(f"Error polling status (will retry): {exc}")
            return
        raise ConversationRunError(self._id, exc) from exc

    def _get_last_error_detail(self) -> str | None:
        """Return the most recent ConversationErrorEvent detail, if available."""
        events = self._state.events
        for idx in range(len(events) - 1, -1, -1):
            event = events[idx]
            if isinstance(event, ConversationErrorEvent):
                detail = event.detail.strip()
                code = event.code.strip()
                if detail and code:
                    return f"{code}: {detail}"
                return detail or code or None

    def set_confirmation_policy(self, policy: ConfirmationPolicyBase) -> None:
        payload = {"policy": policy.model_dump()}
        _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/confirmation_policy",
            json=payload,
        )

    def set_security_analyzer(self, analyzer: SecurityAnalyzerBase | None) -> None:
        """Set the security analyzer for the remote conversation."""
        payload = {
            "security_analyzer": analyzer.model_dump(mode="json")
            if analyzer
            else analyzer
        }
        _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/security_analyzer",
            json=payload,
        )

    def reject_pending_actions(self, reason: str = "User rejected the action") -> None:
        # Equivalent to rejecting confirmation: pause
        _send_request(
            self._client,
            "POST",
            (
                f"{self._conversation_action_base_path}/{self._id}"
                "/events/respond_to_confirmation"
            ),
            json={"accept": False, "reason": reason},
        )

    def pause(self) -> None:
        _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/pause",
        )

    def interrupt(self) -> None:
        _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/interrupt",
        )

    def load_plugin(self, plugin_ref: str) -> None:
        _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/load_plugin",
            json={"plugin_ref": plugin_ref},
        )

    def update_secrets(self, secrets: Mapping[str, SecretValue]) -> None:
        from openhands.sdk.secret.secrets import SecretSource

        serializable_secrets: dict[str, str | dict] = {}
        for key, value in secrets.items():
            if isinstance(value, SecretSource):
                # Pydantic model → dict with "kind" discriminator for server.
                # expose_secrets=True prevents SecretStr fields (e.g. header
                # values) from being redacted during serialization.
                serializable_secrets[key] = value.model_dump(
                    mode="json", context={"expose_secrets": True}
                )
            elif callable(value):
                serializable_secrets[key] = value()
            else:
                serializable_secrets[key] = value

        payload = {"secrets": serializable_secrets}
        _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/secrets",
            json=payload,
        )

    def ask_agent(self, question: str) -> str:
        """Ask the agent a simple, stateless question and get a direct LLM response.

        This bypasses the normal conversation flow and does **not** modify, persist,
        or become part of the conversation state. The request is not remembered by
        the main agent, no events are recorded, and execution status is untouched.
        It is also thread-safe and may be called while `conversation.run()` is
        executing in another thread.

        Args:
            question: A simple string question to ask the agent

        Returns:
            A string response from the agent
        """
        # For remote conversations, delegate to the server endpoint
        payload = {"question": question}

        resp = _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/ask_agent",
            json=payload,
        )
        data = resp.json()
        return data["response"]

    @observe(name="conversation.generate_title", ignore_inputs=["llm"])
    def generate_title(self, llm: LLM | None = None, max_length: int = 50) -> str:
        """Generate a title for the conversation based on the first user message.

        Args:
            llm: Optional LLM to use for title generation. If not provided,
                 uses the agent's LLM.
            max_length: Maximum length of the generated title.

        Returns:
            A generated title for the conversation.
        """
        # Reconcile before reading state so recently posted user messages are
        # visible even if they arrived between the last sync and this call.
        self._state.events.reconcile()

        effective_llm = llm if llm is not None else self.agent.llm
        return generate_conversation_title(
            events=self._state.events, llm=effective_llm, max_length=max_length
        )

    def condense(self) -> None:
        """Force condensation of the conversation history.

        This method sends a condensation request to the remote agent server.
        The server will use the existing condensation request pattern to trigger
        condensation if a condenser is configured and handles condensation requests.

        The condensation will be applied on the server side and will modify the
        conversation state by adding a condensation event to the history.

        Raises:
            HTTPError: If the server returns an error (e.g., no condenser configured).
        """
        _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/condense",
        )

    def fork(
        self,
        *,
        conversation_id: "ConversationID | None" = None,
        agent: "AgentBase | None" = None,
        title: str | None = None,
        tags: dict[str, str] | None = None,
        reset_metrics: bool = True,
        from_event_id: EventID | None = None,
    ) -> "RemoteConversation":
        """Fork this conversation on the remote agent server.

        Sends a fork request to the server which deep-copies events and
        state. Returns a new ``RemoteConversation`` pointing at the fork.

        Args:
            conversation_id: ID for the forked conversation (auto-generated
                on the server if ``None``).
            agent: **Not supported for remote conversations.** Passing a
                non-``None`` value raises ``NotImplementedError``. Use
                ``LocalConversation.fork(agent=...)`` for agent replacement.
            title: Optional title for the forked conversation.
            tags: Optional tags for the forked conversation.
            reset_metrics: If ``True`` (default), cost/token stats start
                fresh on the fork.
            from_event_id: If set, fork only the branch up to and including this
                event (``path_to_root``) and set the fork's HEAD there. If
                ``None`` (default), copy the whole conversation.

        Returns:
            A new ``RemoteConversation`` backed by the forked server-side
            conversation.

        Raises:
            NotImplementedError: If ``agent`` is provided.
            httpx.HTTPStatusError: If the server rejects the request (e.g. 404
                for an unknown ``from_event_id``).
        """
        if agent is not None:
            raise NotImplementedError(
                "Agent replacement is not supported for remote conversation "
                "forks. Use LocalConversation.fork(agent=...) instead."
            )

        body: dict[str, object] = {"reset_metrics": reset_metrics}
        if conversation_id is not None:
            body["id"] = str(conversation_id)
        if title is not None:
            body["title"] = title
        if tags is not None:
            body["tags"] = tags
        if from_event_id is not None:
            body["from_event_id"] = from_event_id

        resp = _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/fork",
            json=body,
        )
        fork_info = resp.json()
        fork_uuid = uuid.UUID(fork_info["id"])

        agent_cls = type(self.agent)
        fork_agent = agent_cls.model_validate(
            self.agent.model_dump(context={"expose_secrets": True}),
        )

        # Use server-returned tags (which include merged title) rather than
        # the input tags, so the client-side object stays consistent.
        server_tags: dict[str, str] | None = fork_info.get("tags") or None

        return RemoteConversation(
            agent=fork_agent,
            workspace=self.workspace,
            conversation_id=fork_uuid,
            max_iteration_per_run=self.max_iteration_per_run,
            delete_on_close=self.delete_on_close,
            tags=server_tags,
        )

    def navigate_to(self, event_id: EventID | None) -> None:
        """Move the conversation HEAD to an existing event on the remote server.

        Posts to the server's ``/navigate`` route, which re-roots the active
        branch in place (no new conversation). The cached state is refreshed so
        a subsequent ``state`` read reflects the new HEAD — ``leaf_event_id`` is
        not broadcast over the WebSocket.

        Args:
            event_id: Event to make the new HEAD, or ``None`` for the empty tree.

        Raises:
            httpx.HTTPStatusError: If the server rejects the event id (e.g. 404
                for an unknown event).
        """
        _send_request(
            self._client,
            "POST",
            f"{self._conversation_action_base_path}/{self._id}/navigate",
            json={"event_id": event_id},
        )
        self._state.refresh_from_server()

    def execute_tool(self, tool_name: str, action: "Action") -> "Observation":
        """Execute a tool directly without going through the agent loop.

        Note: This method is not yet supported for RemoteConversation.
        Tool execution for remote conversations happens on the server side
        during the normal agent loop.

        Args:
            tool_name: The name of the tool to execute
            action: The action to pass to the tool executor

        Raises:
            NotImplementedError: Always, as this feature is not yet supported
                for remote conversations.
        """
        raise NotImplementedError(
            "execute_tool is not yet supported for RemoteConversation. "
            "Tool execution for remote conversations happens on the server side "
            "during the normal agent loop. Use LocalConversation for direct "
            "tool execution."
        )

    def close(self) -> None:
        """Close the conversation and clean up resources.

        Note: We don't close self._client here because it's shared with the workspace.
        The workspace owns the client and will close it during its own cleanup.
        Closing it here would prevent the workspace from making cleanup API calls.
        """
        if self._cleanup_initiated:
            return
        self._cleanup_initiated = True
        # SessionEnd hooks are executed server-side (via hook_config in payload).
        try:
            # Stop WebSocket client if it exists
            if self._ws_client:
                self._ws_client.stop()
                self._ws_client = None
        except Exception:
            pass

        self._end_observability_span()
        if self.delete_on_close:
            try:
                # trigger server-side delete_conversation to release resources
                # like tmux sessions
                _send_request(
                    self._client,
                    "DELETE",
                    f"{self._conversation_action_base_path}/{self.id}",
                )
            except Exception:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
