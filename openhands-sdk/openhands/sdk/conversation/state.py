# state.py
import json
import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from enum import Enum
from pathlib import Path
from typing import Any, Self

from pydantic import Field, PrivateAttr

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context.view import View
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.event_store import ROOT_PARENT_ID, EventLog
from openhands.sdk.conversation.fifo_lock import FIFOLock
from openhands.sdk.conversation.persistence_const import BASE_STATE, EVENTS_DIR
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationID,
    ConversationTags,
)
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.event.base import Event
from openhands.sdk.event.types import EventID
from openhands.sdk.hooks import HookConfig
from openhands.sdk.io import FileStore, InMemoryFileStore, LocalFileStore
from openhands.sdk.logger import get_logger
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    ConfirmationPolicyBase,
    NeverConfirm,
)
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.models import OpenHandsModel
from openhands.sdk.workspace.base import BaseWorkspace


logger = get_logger(__name__)


class ConversationExecutionStatus(str, Enum):
    """Enum representing the current execution state of the conversation."""

    IDLE = "idle"  # Conversation is ready to receive tasks
    RUNNING = "running"  # Conversation is actively processing
    PAUSED = "paused"  # Conversation execution is paused by user
    WAITING_FOR_CONFIRMATION = (
        "waiting_for_confirmation"  # Conversation is waiting for user confirmation
    )
    FINISHED = "finished"  # Conversation has completed the current task
    ERROR = "error"  # Conversation encountered an error (optional for future use)
    STUCK = "stuck"  # Conversation is stuck in a loop or unable to proceed
    DELETING = "deleting"  # Conversation is in the process of being deleted

    def is_terminal(self) -> bool:
        """Check if this status represents a terminal state.

        Terminal states indicate the run has completed and the agent is no longer
        actively processing. These are: FINISHED, ERROR, STUCK.

        Note: IDLE is NOT a terminal state - it's the initial state of a conversation
        before any run has started. Including IDLE would cause false positives when
        the WebSocket delivers the initial state update during connection.

        Returns:
            True if this is a terminal status, False otherwise.
        """
        return self in (
            ConversationExecutionStatus.FINISHED,
            ConversationExecutionStatus.ERROR,
            ConversationExecutionStatus.STUCK,
        )


class ConversationState(OpenHandsModel):
    # ===== Public, validated fields =====
    id: ConversationID = Field(description="Unique conversation ID")

    agent: AgentBase = Field(
        ...,
        description=(
            "The agent running in the conversation. "
            "This is persisted to allow resuming conversations and "
            "check agent configuration to handle e.g., tool changes, "
            "LLM changes, etc."
        ),
    )
    workspace: BaseWorkspace = Field(
        ...,
        description=(
            "Workspace used by the agent to execute commands and read/write files. "
            "Not the process working directory."
        ),
    )
    persistence_dir: str | None = Field(
        default="workspace/conversations",
        description="Directory for persisting conversation state and events. "
        "If None, conversation will not be persisted.",
    )

    max_iterations: int = Field(
        default=500,
        gt=0,
        description="Maximum number of iterations the agent can "
        "perform in a single run.",
    )
    stuck_detection: bool = Field(
        default=True,
        description="Whether to enable stuck detection for the agent.",
    )

    # Enum-based state management
    execution_status: ConversationExecutionStatus = Field(
        default=ConversationExecutionStatus.IDLE
    )
    confirmation_policy: ConfirmationPolicyBase = NeverConfirm()
    security_analyzer: SecurityAnalyzerBase | None = Field(
        default=None,
        description="Optional security analyzer to evaluate action risks.",
    )

    activated_knowledge_skills: list[str] = Field(
        default_factory=list,
        description="List of activated knowledge skills name",
    )

    invoked_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Names of progressive-disclosure skills explicitly invoked via the "
            "`invoke_skill` tool. Parallel to `activated_knowledge_skills`, "
            "which tracks trigger-based activations."
        ),
    )

    # Hook-blocked actions: action_id -> blocking reason
    blocked_actions: dict[str, str] = Field(
        default_factory=dict,
        description="Actions blocked by PreToolUse hooks, keyed by action ID",
    )

    # Hook-blocked messages: message_id -> blocking reason
    blocked_messages: dict[str, str] = Field(
        default_factory=dict,
        description="Messages blocked by UserPromptSubmit hooks, keyed by message ID",
    )

    # Track the most recent user MessageEvent ID to avoid event log scans.
    last_user_message_id: EventID | None = Field(
        default=None,
        description=(
            "Most recent user MessageEvent id for hook block checks. "
            "Updated when user messages are emitted so Agent.step can pop "
            "blocked_messages without scanning the event log. If None, "
            "hook-blocked checks are skipped (legacy conversations)."
        ),
    )

    # Movable HEAD of the conversation tree.
    leaf_event_id: EventID | None = Field(
        default=None,
        description=(
            "HEAD of the conversation tree: the parent of the next appended "
            "event. None means an empty tree (or, pre-feature, the linear tail; "
            "see _resolve_active_leaf). Moving it re-roots the active branch."
        ),
    )

    # Distinguishes a deliberate empty HEAD (navigate_to(None)) from an unset
    # ``leaf_event_id``, which triggers legacy back-compat. Without it, emptying a
    # single-root tree is indistinguishable from a pre-tree conversation. Persisted
    # so the empty HEAD survives a save/reload. See _resolve_active_leaf.
    head_is_empty: bool = Field(default=False)

    # Conversation statistics for LLM usage tracking
    stats: ConversationStats = Field(
        default_factory=ConversationStats,
        description="Conversation statistics for tracking LLM metrics",
    )

    # Secret registry for handling sensitive data
    secret_registry: SecretRegistry = Field(
        default_factory=SecretRegistry,
        description="Registry for handling secrets and sensitive data",
    )

    # User-defined tags (key-value metadata)
    tags: ConversationTags = Field(
        default_factory=dict,
        description="User-defined key-value tags for the conversation. "
        "Keys must be lowercase alphanumeric. Values are arbitrary strings "
        "up to 256 characters.",
    )

    # Agent-specific runtime state (simple dict for flexibility)
    agent_state: dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionary for agent-specific runtime state that persists across "
        "iterations. Agents can store feature-specific state using string keys. "
        "To trigger autosave, always reassign: "
        "state.agent_state = {**state.agent_state, key: value}. "
        "See https://docs.openhands.dev/sdk/guides/convo-persistence#how-state-persistence-works",
    )

    # Hook configuration for the conversation
    hook_config: HookConfig | None = Field(
        default=None,
        description=(
            "Hook configuration for this conversation. Includes definitions for "
            "PreToolUse, PostToolUse, UserPromptSubmit, SessionStart, SessionEnd, "
            "and Stop hooks. When set, these hooks are executed at the appropriate "
            "points during conversation execution."
        ),
    )

    # ===== Private attrs (NOT Fields) =====
    _fs: FileStore = PrivateAttr()  # filestore for persistence
    _events: EventLog = PrivateAttr()  # now the storage for events
    # Cached projection of `_events` for the *active branch*, lazily updated on
    # read. Derived state — never persisted. `_view_branch_leaf` is the resolved
    # leaf the cache reflects: equal to the active leaf -> reuse; an ancestor ->
    # extend with the tail; otherwise -> rebuild (branch switch).
    # See https://github.com/OpenHands/software-agent-sdk/issues/3053.
    _view: View = PrivateAttr(default_factory=View)
    _view_branch_leaf: EventID | None = PrivateAttr(default=None)
    _view_lock: threading.RLock = PrivateAttr(default_factory=threading.RLock)
    _cipher: Cipher | None = PrivateAttr(default=None)  # cipher for secret encryption
    _autosave_enabled: bool = PrivateAttr(
        default=False
    )  # to avoid recursion during init
    _on_state_change: ConversationCallbackType | None = PrivateAttr(
        default=None
    )  # callback for state changes
    _write_guard: Callable[[], AbstractContextManager[None]] | None = PrivateAttr(
        default=None
    )
    _lock: FIFOLock = PrivateAttr(
        default_factory=FIFOLock
    )  # FIFO lock for thread safety
    _save_depth: int = PrivateAttr(default=0)  # context-manager nesting depth
    _dirty: bool = PrivateAttr(default=False)  # pending unsaved field changes

    @property
    def events(self) -> EventLog:
        return self._events

    def _resolve_active_leaf(self) -> EventID | None:
        """The effective HEAD, resolving a ``None`` ``leaf_event_id`` by lineage.

        A set leaf is returned as-is. ``head_is_empty`` marks a deliberate empty
        HEAD (``navigate_to(None)``) -> no active branch. Otherwise a ``None`` leaf
        falls back to the last event only for pre-feature conversations (last
        event has no ``parent_id``), so legacy history loads unbranched.
        """
        if self.leaf_event_id is not None:
            return self.leaf_event_id
        if self.head_is_empty:
            return None
        if (n := len(self._events)) == 0:
            return None
        if self._events[n - 1].parent_id is not None:
            return None
        return self._events.get_id(n - 1)

    def active_branch(self, limit: int | None = None) -> list[Event]:
        """Raw events on the active branch (``path_to_root(leaf)``), root-first.

        Excludes abandoned branches, so consumers reasoning about the *current*
        conversation (stuck detection, pending actions) see only the live path.
        ``limit`` returns just the last ``limit`` events, kept O(limit).
        """
        return self._events.path_to_root(self._resolve_active_leaf(), limit=limit)

    def _stamp_parent_id(self, event: Event) -> Event:
        """Return ``event`` with ``parent_id`` set to the active leaf if unset."""
        if event.parent_id is not None:
            return event
        parent = self._resolve_active_leaf()
        # Empty HEAD over a non-empty log = deliberate new root (navigate_to(None));
        # mark it so it is not misread as a legacy event chained to its neighbour.
        if parent is None and len(self._events) > 0:
            parent = ROOT_PARENT_ID
        return event.model_copy(update={"parent_id": parent})

    def append_event(self, event: Event) -> Event:
        """Single storage chokepoint: stamp parent_id, append, advance HEAD.

        Stamping here (not only at the emit callback) ensures no event enters the
        log unstamped, even one a hook swaps in downstream. ``fork`` copies
        pre-stamped events and sets HEAD itself, so it bypasses this.
        """
        event = self._stamp_parent_id(event)
        self._events.append(event)
        # ConversationStateUpdateEvent is a state-sync artifact, not a tree node;
        # advancing HEAD for it would recurse (moving HEAD re-emits one).
        from openhands.sdk.event.conversation_state import (
            ConversationStateUpdateEvent,
        )

        if not isinstance(event, ConversationStateUpdateEvent):
            self.leaf_event_id = event.id
            if self.head_is_empty:  # HEAD now points at a real event again
                self.head_is_empty = False
        return event

    @property
    def view(self) -> View:
        """Lazily-maintained ``View`` of the active branch (``path_to_root(leaf)``).

        Abandoned branches (from navigation/forking) are excluded. A linear
        append replays only the new tail — O(k), preserving the incremental
        optimization (#3053); a branch switch rebuilds once, O(n).
        ``enforce_properties`` runs only on rebuild (``rebuild_view()``).

        The returned view is read-only and is invalidated by ``rebuild_view()``;
        re-read ``state.view`` after any rebuild.
        """
        with self._view_lock:
            leaf = self._resolve_active_leaf()
            if leaf == self._view_branch_leaf:
                return self._view

            # Fast path: cached leaf is an ancestor of the new leaf (linear
            # append) → replay only the tail. Also covers first populate (cached
            # leaf None, cached view empty), avoiding the enforce_properties pass.
            try:
                tail: list[Event] = []
                cur_id: EventID | None = leaf
                while cur_id is not None and cur_id != self._view_branch_leaf:
                    idx = self._events.get_index(cur_id)
                    evt = self._events[idx]
                    tail.append(evt)
                    cur_id = self._events._effective_parent_id(idx, evt)
                if cur_id == self._view_branch_leaf:
                    for evt in reversed(tail):
                        self._view.append_event(evt)
                    self._view_branch_leaf = leaf
                    return self._view
            except Exception:
                logger.warning(
                    "Incremental view append failed for leaf %s; "
                    "rebuilding from the active branch.",
                    leaf,
                    exc_info=True,
                )

            # Diverged branch (navigation/fork), first populate, or recovery
            # from the failure above → full rebuild from the active branch.
            self._view = View.from_events(self._events.path_to_root(leaf))
            self._view_branch_leaf = leaf
            return self._view

    def rebuild_view(self) -> None:
        """Re-derive the cached view from the active branch, with full enforcement.

        Runs ``View.from_events`` over ``path_to_root(leaf)``. Called on cold
        load, fork, navigation, and error recovery. Invalidates any prior
        ``state.view`` reference. If ``View.from_events`` raises, the cache is
        left unchanged and the exception propagates.
        """
        with self._view_lock:
            leaf = self._resolve_active_leaf()
            branch = self._events.path_to_root(leaf)
            self._view = View.from_events(branch)
            self._view_branch_leaf = leaf

    @property
    def env_observation_persistence_dir(self) -> str | None:
        """Directory for persisting environment observation files."""
        if self.persistence_dir is None:
            return None
        return str(Path(self.persistence_dir) / "observations")

    def set_on_state_change(self, callback: ConversationCallbackType | None) -> None:
        """Set a callback to be called when state changes.

        Args:
            callback: A function that takes an Event (ConversationStateUpdateEvent)
                     or None to remove the callback
        """
        self._on_state_change = callback

    def set_write_guard(
        self,
        write_guard: Callable[[], AbstractContextManager[None]] | None,
    ) -> None:
        self._write_guard = write_guard
        self._events.set_write_guard(write_guard)

    # ===== Base snapshot helpers (same FileStore usage you had) =====
    def _save_base_state(self, fs: FileStore) -> None:
        """
        Persist base state snapshot (no events; events are file-backed).

        If a cipher is configured, secrets will be encrypted. Otherwise, they
        will be redacted (serialized as '**********').
        """
        context = {"cipher": self._cipher} if self._cipher else None
        # Warn if secrets exist but no cipher is configured
        if not self._cipher and self.secret_registry.secret_sources:
            logger.warning(
                f"Saving conversation state without cipher - "
                f"{len(self.secret_registry.secret_sources)} secret(s) will be "
                "redacted and lost on restore. Consider providing a cipher to "
                "preserve secrets."
            )
        payload = self.model_dump_json(exclude_none=True, context=context)
        if self._write_guard is None:
            fs.write(BASE_STATE, payload)
        else:
            with self._write_guard():
                fs.write(BASE_STATE, payload)

    # ===== Factory: open-or-create (no load/save methods needed) =====
    @classmethod
    def create(
        cls: type["ConversationState"],
        id: ConversationID,
        agent: AgentBase,
        workspace: BaseWorkspace,
        persistence_dir: str | None = None,
        max_iterations: int = 500,
        stuck_detection: bool = True,
        cipher: Cipher | None = None,
        tags: dict[str, str] | None = None,
        file_store: FileStore | None = None,
    ) -> "ConversationState":
        """Create a new conversation state or resume from persistence.

        This factory method handles both new conversation creation and resumption
        from persisted state.

        **New conversation:**
        The provided Agent is used directly. Pydantic validation happens via the
        cls() constructor.

        **Restored conversation:**
        The provided Agent is validated against the persisted agent using
        agent.load(). Tools must match (they may have been used in conversation
        history), but all other configuration can be freely changed: LLM,
        agent_context, condenser, system prompts, etc.

        Args:
            id: Unique conversation identifier
            agent: The Agent to use (tools must match persisted on restore)
            workspace: Working directory for agent operations
            persistence_dir: Directory for persisting state and events when
                file_store is not provided. When file_store is provided, this
                value is still stored on the state and used for environment
                observation paths.
            max_iterations: Maximum iterations per run
            stuck_detection: Whether to enable stuck detection
            cipher: Optional cipher for encrypting/decrypting secrets in
                    persisted state. If provided, secrets are encrypted when
                    saving and decrypted when loading. If not provided, secrets
                    are redacted (lost) on serialization.
            tags: Optional key-value tags for the conversation. Keys must be
                  lowercase alphanumeric, values up to 256 characters.
            file_store: Optional FileStore to use for state and EventLog
                persistence. If provided, this takes precedence over
                persistence_dir for state and EventLog storage.

        Returns:
            ConversationState ready for use

        Raises:
            ValueError: If conversation ID or tools mismatch on restore
            ValidationError: If agent or other fields fail Pydantic validation
        """
        if file_store is None:
            if persistence_dir:
                file_store = LocalFileStore(
                    persistence_dir, cache_limit_size=max_iterations
                )
            else:
                logger.warning(
                    "No persistence_dir provided; falling back to InMemoryFileStore. "
                    "EventLog data will not persist across requests."
                )
                file_store = InMemoryFileStore()

        try:
            base_text = file_store.read(BASE_STATE)
        except FileNotFoundError:
            base_text = None

        # ---- Resume path ----
        if base_text:
            # Use cipher context for decrypting secrets if provided
            context = {"cipher": cipher} if cipher else None
            state = cls.model_validate(json.loads(base_text), context=context)

            # Restore the conversation with the same id
            if state.id != id:
                raise ValueError(
                    f"Conversation ID mismatch: provided {id}, "
                    f"but persisted state has {state.id}"
                )

            # Attach event log early so we can read history for tool verification
            state._fs = file_store
            state._events = EventLog(file_store, dir_path=EVENTS_DIR)
            state._cipher = cipher

            # Cold-load: rebuild the cached view with full property
            # enforcement — persisted events may come from an older code
            # version or be corrupted.
            state.rebuild_view()

            # Verify compatibility (agent class + tools)
            agent.verify(state.agent, events=state._events)

            # Commit runtime-provided values (may autosave)
            state._autosave_enabled = True
            state.agent = agent
            state.workspace = workspace
            state.max_iterations = max_iterations

            # Note: stats are already deserialized from base_state.json above.
            # Do NOT reset stats here - this would lose accumulated metrics.

            logger.info("Resumed conversation %s from persistent storage", state.id)
            return state

        # ---- Fresh path ----
        if agent is None:
            raise ValueError(
                "agent is required when initializing a new ConversationState"
            )

        state = cls(
            id=id,
            agent=agent,
            workspace=workspace,
            persistence_dir=persistence_dir,
            max_iterations=max_iterations,
            stuck_detection=stuck_detection,
            tags=tags or {},
        )
        state._fs = file_store
        state._events = EventLog(file_store, dir_path=EVENTS_DIR)
        state._cipher = cipher
        state.stats = ConversationStats()

        state._save_base_state(file_store)  # initial snapshot
        state._autosave_enabled = True
        logger.info("Created new conversation %s", state.id)
        return state

    # ===== Auto-persist base on public field changes =====
    def __setattr__(self, name, value):
        # Only autosave when:
        # - autosave is enabled (set post-init)
        # - the attribute is a *public field* (not a PrivateAttr)
        # - we have a filestore to write to
        _sentinel = object()
        old = getattr(self, name, _sentinel)
        super().__setattr__(name, value)

        is_field = name in self.__class__.model_fields
        autosave_enabled = getattr(self, "_autosave_enabled", False)
        fs = getattr(self, "_fs", None)

        if not (autosave_enabled and is_field and fs is not None):
            return

        if old is _sentinel or old != value:
            # Inside a context-manager block, defer the save until __exit__
            # so that multiple field mutations produce a single I/O write.
            if getattr(self, "_save_depth", 0) > 0:
                self._dirty = True
            else:
                try:
                    self._save_base_state(fs)
                except Exception as e:
                    logger.exception("Auto-persist base_state failed", exc_info=True)
                    raise e

            # Call state change callback if set. leaf_event_id/head_is_empty are
            # skipped: they are internal HEAD bookkeeping (leaf_event_id changes
            # every event, so broadcasting it would ~double the persisted log) and
            # the HEAD is recoverable from the just-appended event.
            callback = getattr(self, "_on_state_change", None)
            if (
                callback is not None
                and old is not _sentinel
                and name not in ("leaf_event_id", "head_is_empty")
            ):
                try:
                    # Import here to avoid circular imports
                    from openhands.sdk.event.conversation_state import (
                        ConversationStateUpdateEvent,
                    )

                    # Create a ConversationStateUpdateEvent with the changed field
                    state_update_event = ConversationStateUpdateEvent(
                        key=name, value=value
                    )
                    callback(state_update_event)
                except Exception:
                    logger.exception(
                        f"State change callback failed for field {name}", exc_info=True
                    )

    def block_action(self, action_id: str, reason: str) -> None:
        """Persistently record a hook-blocked action."""
        self.blocked_actions = {**self.blocked_actions, action_id: reason}

    def pop_blocked_action(self, action_id: str) -> str | None:
        """Remove and return a hook-blocked action reason, if present."""
        if action_id not in self.blocked_actions:
            return None
        updated = dict(self.blocked_actions)
        reason = updated.pop(action_id)
        self.blocked_actions = updated
        return reason

    def block_message(self, message_id: str, reason: str) -> None:
        """Persistently record a hook-blocked user message."""
        self.blocked_messages = {**self.blocked_messages, message_id: reason}

    def pop_blocked_message(self, message_id: str) -> str | None:
        """Remove and return a hook-blocked message reason, if present."""
        if message_id not in self.blocked_messages:
            return None
        updated = dict(self.blocked_messages)
        reason = updated.pop(message_id)
        self.blocked_messages = updated
        return reason

    @staticmethod
    def get_unmatched_actions(events: Sequence[Event]) -> list[ActionEvent]:
        """Find actions in the event history that don't have matching observations.

        This method identifies ActionEvents that don't have corresponding
        ObservationEvents, UserRejectObservations, or AgentErrorEvents,
        which typically indicates actions that are pending confirmation or execution.

        Note: AgentErrorEvent is matched by tool_call_id (not action_id) because
        it doesn't have an action_id field. This is important for crash recovery
        scenarios where an error event is emitted after a server restart.

        Args:
            events: List of events to search through

        Returns:
            List of ActionEvent objects that don't have corresponding observations,
            in chronological order
        """
        observed_action_ids: set[EventID] = set()
        observed_tool_call_ids: set[str] = set()
        unmatched_actions = []
        # Search in reverse - recent events are more likely to be unmatched
        for event in reversed(events):
            if isinstance(event, (ObservationEvent, UserRejectObservation)):
                observed_action_ids.add(event.action_id)
            elif isinstance(event, AgentErrorEvent):
                # AgentErrorEvent doesn't have action_id, match by tool_call_id
                observed_tool_call_ids.add(event.tool_call_id)
            elif isinstance(event, ActionEvent):
                # Only executable actions (validated) are considered pending
                # Check both action_id and tool_call_id for matching
                if (
                    event.action is not None
                    and event.id not in observed_action_ids
                    and event.tool_call_id not in observed_tool_call_ids
                ):
                    # Insert at beginning to maintain chronological order in result
                    unmatched_actions.insert(0, event)

        return unmatched_actions

    # ===== FIFOLock delegation methods =====
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """
        Acquire the lock.

        Args:
            blocking: If True, block until lock is acquired. If False, return
                     immediately.
            timeout: Maximum time to wait for lock (ignored if blocking=False).
                    -1 means wait indefinitely.

        Returns:
            True if lock was acquired, False otherwise.
        """
        return self._lock.acquire(blocking=blocking, timeout=timeout)

    def release(self) -> None:
        """
        Release the lock.

        Raises:
            RuntimeError: If the current thread doesn't own the lock.
        """
        self._lock.release()

    def __enter__(self: Self) -> Self:
        """Context manager entry.

        Field mutations inside the ``with`` block are batched: the state
        is persisted at most once, on exit, instead of on every assignment.
        """
        self._lock.acquire()
        self._save_depth += 1
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit — flushes any deferred save."""
        try:
            self._save_depth -= 1
            if self._save_depth == 0 and self._dirty:
                fs = getattr(self, "_fs", None)
                autosave_enabled = getattr(self, "_autosave_enabled", False)
                if autosave_enabled and fs is not None:
                    self._save_base_state(fs)
                self._dirty = False
        finally:
            self._lock.release()

    def locked(self) -> bool:
        """
        Return True if the lock is currently held by any thread.
        """
        return self._lock.locked()

    def owned(self) -> bool:
        """
        Return True if the lock is currently held by the calling thread.
        """
        return self._lock.owned()
