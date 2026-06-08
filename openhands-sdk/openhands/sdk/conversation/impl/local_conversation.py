import asyncio
import atexit
import contextlib
import copy
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TypeGuard

from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context.prompts.prompt import render_template
from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.conversation.cancellation import CancellationToken
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.secret_registry import SecretValue
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.conversation.stuck_detector import StuckDetector
from openhands.sdk.conversation.title_utils import generate_conversation_title
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationID,
    ConversationTokenCallbackType,
    StuckDetectionThresholds,
)
from openhands.sdk.conversation.visualizer import (
    ConversationVisualizerBase,
    DefaultConversationVisualizer,
)
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    CondensationRequest,
    Event,
    InterruptEvent,
    MessageEvent,
    ObservationEvent,
    PauseEvent,
    UserRejectObservation,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.hooks import HookConfig, HookEventProcessor, create_hook_callback
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import LLM, Message, TextContent, content_to_str
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.llm_registry import LLMRegistry
from openhands.sdk.logger import get_logger
from openhands.sdk.observability.laminar import observe
from openhands.sdk.plugin import (
    Plugin,
    PluginSource,
    ResolvedPluginSource,
    fetch_plugin_with_resolution,
)
from openhands.sdk.secret import StaticSecret
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    ConfirmationPolicyBase,
)
from openhands.sdk.skills import load_available_skills, merge_skills_by_name
from openhands.sdk.skills.utils import expand_mcp_variables
from openhands.sdk.subagent import (
    AgentDefinition,
    register_file_agents,
    register_plugin_agents,
)
from openhands.sdk.tool.client_tool import ClientToolSpec
from openhands.sdk.tool.schema import Action, Observation
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


logger = get_logger(__name__)

ACP_LAST_PROMPT_USER_MESSAGE_ID = "acp_last_prompt_user_message_id"
ACP_INFLIGHT_PROMPT_USER_MESSAGE_ID = "acp_inflight_prompt_user_message_id"
ACP_SUPERSEDE_INFLIGHT_PROMPT = "acp_supersede_inflight_prompt"
ACP_STOP_HOOK_FEEDBACK_PREFIX = "[Stop hook feedback]"


def _is_acp_prompt_message(event: Event) -> TypeGuard[MessageEvent]:
    if not isinstance(event, MessageEvent):
        return False
    if event.source == "user":
        return True
    if event.source != "environment" or event.llm_message.role != "user":
        return False
    return any(
        part.startswith(ACP_STOP_HOOK_FEEDBACK_PREFIX)
        for part in content_to_str(event.llm_message.content)
    )


def _copy_event_for_fork(event: Event) -> Event:
    # Mirrors persisted event loading and skips runtime-only fields like executors.
    return Event.model_validate_json(event.model_dump_json(exclude_none=True))


class LocalConversation(BaseConversation):
    agent: AgentBase
    workspace: LocalWorkspace
    _state: ConversationState
    _visualizer: ConversationVisualizerBase | None
    _on_event: ConversationCallbackType
    _on_token: ConversationTokenCallbackType | None
    max_iteration_per_run: int
    _stuck_detector: StuckDetector | None
    llm_registry: LLMRegistry
    _cleanup_initiated: bool
    _hook_processor: HookEventProcessor | None
    delete_on_close: bool = True
    _arun_task: asyncio.Task[None] | None
    _cancel_token: CancellationToken | None
    # True while run()/arun() executes an agent step while holding the state
    # lock. A state-mutating tool (e.g. switch_llm) runs on a worker thread, so
    # it must skip re-acquiring the lock the run loop holds while blocked
    # awaiting that tool (#3485).
    _step_holds_state_lock: bool
    # Plugin lazy loading state
    _plugin_specs: list[PluginSource] | None
    _resolved_plugins: list[ResolvedPluginSource] | None
    _plugins_loaded: bool
    _pending_hook_config: HookConfig | None  # Hook config to combine with plugin hooks

    def __init__(
        self,
        agent: AgentBase,
        workspace: str | Path | LocalWorkspace,
        plugins: list[PluginSource] | None = None,
        persistence_dir: str | Path | None = None,
        conversation_id: ConversationID | None = None,
        callbacks: list[ConversationCallbackType] | None = None,
        token_callbacks: list[ConversationTokenCallbackType] | None = None,
        hook_config: HookConfig | None = None,
        max_iteration_per_run: int = 500,
        stuck_detection: bool = True,
        stuck_detection_thresholds: (
            StuckDetectionThresholds | Mapping[str, int] | None
        ) = None,
        visualizer: (
            type[ConversationVisualizerBase] | ConversationVisualizerBase | None
        ) = DefaultConversationVisualizer,
        secrets: Mapping[str, SecretValue] | None = None,
        delete_on_close: bool = True,
        cipher: Cipher | None = None,
        tags: dict[str, str] | None = None,
        user_id: str | None = None,
        client_tools: list[ClientToolSpec] | None = None,
        **_: object,
    ):
        """Initialize the conversation.

        Args:
            agent: The agent to use for the conversation.
            workspace: Working directory for agent operations and tool execution.
                Can be a string path, Path object, or LocalWorkspace instance.
            plugins: Optional list of plugins to load. Each plugin is specified
                with a source (github:owner/repo, git URL, or local path),
                optional ref (branch/tag/commit), and optional repo_path for
                monorepos. Plugins are loaded in order with these merge
                semantics: skills override by name (last wins), MCP config
                override by key (last wins), hooks concatenate (all run).
            persistence_dir: Directory for persisting conversation state and events.
                Can be a string path or Path object.
            conversation_id: Optional ID for the conversation. If provided, will
                      be used to identify the conversation. The user might want to
                      suffix their persistent filestore with this ID.
            callbacks: Optional list of callback functions to handle events
            token_callbacks: Optional list of callbacks invoked for streaming deltas
            hook_config: Optional hook configuration to auto-wire session hooks.
                If plugins are loaded, their hooks are combined with this config.
            max_iteration_per_run: Maximum number of iterations per run
            visualizer: Visualization configuration. Can be:
                       - ConversationVisualizerBase subclass: Class to instantiate
                         (default: ConversationVisualizer)
                       - ConversationVisualizerBase instance: Use custom visualizer
                       - None: No visualization
            stuck_detection: Whether to enable stuck detection
            stuck_detection_thresholds: Optional configuration for stuck detection
                      thresholds. Can be a StuckDetectionThresholds instance or
                      a dict with keys: 'action_observation', 'action_error',
                      'monologue', 'alternating_pattern'. Values are integers
                      representing the number of repetitions before triggering.
            cipher: Optional cipher for encrypting/decrypting secrets in persisted
                   state. If provided, secrets are encrypted when saving and
                   decrypted when loading. If not provided, secrets are redacted
                   (lost) on serialization.
            tags: Optional key-value tags for the conversation. Keys must be
                  lowercase alphanumeric, values up to 256 characters.
            client_tools: Optional list of client-defined tool specs. Each spec
                  is registered and injected into the agent so it can call the
                  tool; the executor returns an acknowledgment and the real
                  execution is expected to be handled by a callback/consumer
                  (e.g. a frontend) observing the emitted ActionEvent.
        """
        super().__init__()  # Initialize with span tracking
        # Mark cleanup as initiated as early as possible to avoid races or partially
        # initialized instances during interpreter shutdown.
        self._cleanup_initiated = False
        self._arun_task = None
        self._cancel_token = None
        self._step_holds_state_lock = False

        # Store plugin specs for lazy loading (no IO in constructor)
        # Plugins will be loaded on first run() or send_message() call
        self._plugin_specs = plugins
        self._resolved_plugins = None
        self._plugins_loaded = False
        self._pending_hook_config = hook_config  # Will be combined with plugin hooks
        self._agent_ready = False  # Agent initialized lazily after plugins loaded

        # Register any client-defined tools and inject them into the agent so
        # the agent can call them. Execution is deferred to a consumer of the
        # emitted ActionEvent (e.g. a frontend); the executor only acks.
        if client_tools:
            from openhands.sdk.tool.client_tool import register_client_tools

            client_tool_specs = register_client_tools(client_tools)
            existing_names = {t.name for t in agent.tools}
            new_tools = [
                ts for ts in client_tool_specs if ts.name not in existing_names
            ]
            if new_tools:
                agent = agent.model_copy(update={"tools": [*agent.tools, *new_tools]})

        self.agent = agent
        if isinstance(workspace, (str, Path)):
            # LocalWorkspace accepts both str and Path via BeforeValidator
            workspace = LocalWorkspace(working_dir=workspace)
        assert isinstance(workspace, LocalWorkspace), (
            "workspace must be a LocalWorkspace instance"
        )
        self.workspace = workspace
        ws_path = Path(self.workspace.working_dir)
        if not ws_path.exists():
            ws_path.mkdir(parents=True, exist_ok=True)

        # Create-or-resume: factory inspects BASE_STATE to decide
        desired_id = conversation_id or uuid.uuid4()
        self._state = ConversationState.create(
            id=desired_id,
            agent=agent,
            workspace=self.workspace,
            persistence_dir=self.get_persistence_dir(persistence_dir, desired_id)
            if persistence_dir
            else None,
            max_iterations=max_iteration_per_run,
            stuck_detection=stuck_detection,
            cipher=cipher,
            tags=tags,
        )

        self._pin_prompt_cache_key()

        # Default callback: persist every event to state
        def _default_callback(e):
            # This callback runs while holding the conversation state's lock
            # (see BaseConversation.compose_callbacks usage inside `with self._state:`
            # regions), so updating state here is thread-safe.
            self._state.events.append(e)
            # Track user MessageEvent IDs here so hook callbacks (which may
            # synthesize or alter user messages) are captured in one place.
            if isinstance(e, MessageEvent) and e.source == "user":
                # Track the latest real user message ID for hook-blocked checks.
                # Stop-hook feedback is emitted with source="environment".
                self._state.last_user_message_id = e.id

        callback_list = list(callbacks) if callbacks else []
        composed_list = callback_list + [_default_callback]
        # Handle visualization configuration
        if isinstance(visualizer, ConversationVisualizerBase):
            # Use custom visualizer instance
            self._visualizer = visualizer
            # Initialize the visualizer with conversation state
            self._visualizer.initialize(self._state)
            composed_list = [self._visualizer.on_event] + composed_list
            # visualizer should happen first for visibility
        elif isinstance(visualizer, type) and issubclass(
            visualizer, ConversationVisualizerBase
        ):
            # Instantiate the visualizer class with appropriate parameters
            self._visualizer = visualizer()
            # Initialize with state
            self._visualizer.initialize(self._state)
            composed_list = [self._visualizer.on_event] + composed_list
            # visualizer should happen first for visibility
        else:
            # No visualization (visualizer is None)
            self._visualizer = None

        # Compose the base callback chain (visualizer -> user callbacks -> default)
        base_callback = BaseConversation.compose_callbacks(composed_list)
        self._base_callback = base_callback  # Store for _ensure_plugins_loaded

        # Defer all hook setup to _ensure_plugins_loaded() for consistency
        # This runs on first run()/send_message() call and handles both
        # explicit hooks and plugin hooks in one place
        self._hook_processor = None
        self._on_event = base_callback
        self._on_token = (
            BaseConversation.compose_callbacks(token_callbacks)
            if token_callbacks
            else None
        )

        self.max_iteration_per_run = max_iteration_per_run

        # Initialize stuck detector
        if stuck_detection:
            # Convert dict to StuckDetectionThresholds if needed
            if isinstance(stuck_detection_thresholds, Mapping):
                threshold_config = StuckDetectionThresholds(
                    **stuck_detection_thresholds
                )
            else:
                threshold_config = stuck_detection_thresholds
            self._stuck_detector = StuckDetector(
                self._state,
                thresholds=threshold_config,
            )
        else:
            self._stuck_detector = None

        # Agent initialization is deferred to _ensure_agent_ready() for lazy loading
        # This ensures plugins are loaded before agent initialization
        self.llm_registry = LLMRegistry()
        self._profile_store = LLMProfileStore()
        self._cipher = cipher

        # Seed agent_context.secrets into the registry for every agent (regular
        # and ACP), covering callers that skip create_request() — canvas /
        # TypeScript, or the server-side agent_settings -> create_agent fold.
        # Idempotent with the create_request() lift; lower priority than
        # request.secrets (below). On resume, fill-if-absent so a persisted
        # value is never downgraded (or lost to redacted/no-cipher serialization).
        if (ctx := getattr(self.agent, "agent_context", None)) is not None:
            ctx_secrets = getattr(ctx, "secrets", None)
            if ctx_secrets:
                existing_sources = self._state.secret_registry.secret_sources
                fill_secrets: dict[str, SecretValue] = {}
                for name, secret in ctx_secrets.items():
                    existing = existing_sources.get(name)
                    # Refill only when absent, or when a StaticSecret lost its
                    # value to redacted/no-cipher serialization (value is None).
                    # Other SecretSource types (e.g. LookupSecret) are left as-is
                    # even with a None value — that is their resolved state, not
                    # a stale placeholder to overwrite.
                    if existing is None or (
                        isinstance(existing, StaticSecret) and existing.value is None
                    ):
                        fill_secrets[name] = secret
                if fill_secrets:
                    self.update_secrets(fill_secrets)

        # Higher priority: request.secrets overwrites duplicate keys from above.
        if secrets:
            secret_values: dict[str, SecretValue] = {k: v for k, v in secrets.items()}
            self.update_secrets(secret_values)

        atexit.register(self.close)
        self._start_observability_span(str(desired_id), user_id=user_id)
        self.delete_on_close = delete_on_close

    @property
    def id(self) -> ConversationID:
        """Get the unique ID of the conversation."""
        return self._state.id

    @property
    def state(self) -> ConversationState:
        """Get the conversation state.

        It returns a protocol that has a subset of ConversationState methods
        and properties. We will have the ability to access the same properties
        of ConversationState on a remote conversation object.
        But we won't be able to access methods that mutate the state.
        """
        return self._state

    @property
    def conversation_stats(self):
        return self._state.stats

    @property
    def stuck_detector(self) -> StuckDetector | None:
        """Get the stuck detector instance if enabled."""
        return self._stuck_detector

    @property
    def cancel_token(self) -> CancellationToken | None:
        """Active cancellation token for the current run, or ``None``.

        Tools that want cooperative cancellation can check this during
        execution::

            if conversation and conversation.cancel_token:
                if conversation.cancel_token.is_cancelled:
                    return Observation(output="Cancelled")
        """
        return self._cancel_token

    @property
    def resolved_plugins(self) -> list[ResolvedPluginSource] | None:
        """Get the resolved plugin sources after plugins are loaded.

        Returns None if plugins haven't been loaded yet, or if no plugins
        were specified. Use this for persistence to ensure conversation
        resume uses the exact same plugin versions.
        """
        return self._resolved_plugins

    def fork(
        self,
        *,
        conversation_id: ConversationID | None = None,
        agent: AgentBase | None = None,
        title: str | None = None,
        tags: dict[str, str] | None = None,
        reset_metrics: bool = True,
    ) -> "LocalConversation":
        """Deep-copy this conversation with a new ID.

        Events are copied so the source remains immutable. The fork starts
        in ``execution_status='idle'``; calling ``run()`` resumes from the
        copied state — meaning the agent has full event memory of the source.

        Args:
            conversation_id: ID for the forked conversation (auto-generated
                if ``None``).
            agent: Agent for the fork. Defaults to a deep-copy of the
                source agent.
            title: Optional title for the forked conversation.
            tags: Optional tags for the forked conversation.
            reset_metrics: If ``True`` (default), cost/token stats start
                fresh on the fork.

        Returns:
            A new ``LocalConversation`` that shares the same event history
            but has its own identity and independent state going forward.
        """
        fork_id = conversation_id or uuid.uuid4()
        # Always deep-copy the agent (supplied or source) so the fork owns
        # its own object graph. Required because __init__ mutates
        # agent.llm._prompt_cache_key in place (#2917): a shared/aliased
        # agent would clobber the source conversation's cache key.
        # Round-trip via JSON avoids thread-lock pickling issues with
        # model_copy(deep=True).
        source_agent = agent if agent is not None else self.agent
        agent_cls = type(source_agent)
        fork_agent = agent_cls.model_validate(
            source_agent.model_dump(context={"expose_secrets": True}),
        )

        # Hold the state lock while reading mutable state from the source
        # conversation to avoid torn reads if run() is executing concurrently.
        with self._state:
            # Determine persistence_dir for the fork.
            # Pass the *base* directory only — __init__ calls
            # get_persistence_dir() which appends the conversation ID hex,
            # so we must not do that here.
            source_persistence = self._state.persistence_dir
            fork_persistence: str | None = None
            if source_persistence is not None:
                source_path = Path(source_persistence)
                fork_persistence = str(source_path.parent)

            # Build the fork conversation (empty – no events yet)
            fork_conv = LocalConversation(
                agent=fork_agent,
                workspace=self.workspace,
                plugins=self._plugin_specs,
                persistence_dir=fork_persistence,
                conversation_id=fork_id,
                max_iteration_per_run=self.max_iteration_per_run,
                stuck_detection=self._stuck_detector is not None,
                visualizer=type(self._visualizer) if self._visualizer else None,
                delete_on_close=self.delete_on_close,
                tags=tags,
            )

            for event in self._state.events:
                fork_conv._state.events.append(_copy_event_for_fork(event))
            # Full rebuild: the copied events may need property enforcement
            # (same posture as cold load).
            fork_conv._state.rebuild_view()

            # Copy runtime state that accumulated during the source
            # conversation. activated_knowledge_skills is list[str] – strings
            # are immutable so a shallow list copy is sufficient.
            # agent_state can hold arbitrary mutable values, so deep-copy it.
            fork_conv._state.activated_knowledge_skills = list(
                self._state.activated_knowledge_skills
            )
            fork_conv._state.agent_state = copy.deepcopy(self._state.agent_state)

            # Copy title via tags if provided
            if title is not None:
                fork_conv._state.tags = {
                    **fork_conv._state.tags,
                    "title": title,
                }

            # Reset or copy metrics
            if not reset_metrics:
                fork_conv._state.stats = self._state.stats.model_copy(deep=True)

            event_count = len(self._state.events)

        logger.info(
            f"Forked conversation {self.id} → {fork_id} "
            f"({event_count} events copied, "
            f"reset_metrics={reset_metrics})"
        )
        return fork_conv

    def _ensure_plugins_loaded(self) -> None:
        """Lazy load plugins and set up hooks on first use.

        This method is called automatically before run() and send_message().
        It handles both plugin loading and hook initialization in one place
        for consistency.

        The method:
        1. Fetches plugins from their sources (network IO for remote sources)
        2. Resolves refs to commit SHAs for deterministic resume
        3. Loads plugin contents (skills, MCP config, hooks)
        4. Merges plugin contents into the agent
        5. Sets up hook processor with combined hooks (explicit + plugin)
        6. Runs session_start hooks
        """
        if self._plugins_loaded:
            return

        all_plugin_hooks: list[HookConfig] = []
        all_plugin_agents: list[AgentDefinition] = []

        merged_context = self.agent.agent_context
        merged_mcp = dict(self.agent.mcp_config) if self.agent.mcp_config else {}

        # Track whether we have plugins or MCP config to process
        has_mcp_config = bool(merged_mcp)

        # Load plugins if specified
        if self._plugin_specs:
            logger.info(f"Loading {len(self._plugin_specs)} plugin(s)...")
            self._resolved_plugins = []

            for spec in self._plugin_specs:
                # Fetch plugin and get resolved commit SHA
                path, resolved_ref = fetch_plugin_with_resolution(
                    source=spec.source,
                    ref=spec.ref,
                    repo_path=spec.repo_path,
                )

                # Store resolved ref for persistence
                resolved = ResolvedPluginSource.from_plugin_source(spec, resolved_ref)
                self._resolved_plugins.append(resolved)

                # Load the plugin
                plugin = Plugin.load(path)
                logger.debug(
                    f"Loaded plugin '{plugin.manifest.name}' from {spec.source}"
                    + (f" @ {resolved_ref[:8]}" if resolved_ref else "")
                )

                # Merge plugin contents
                merged_context = plugin.add_skills_to(merged_context)
                merged_mcp = plugin.add_mcp_config_to(merged_mcp)
                has_mcp_config = has_mcp_config or bool(merged_mcp)

                # Collect hooks
                if plugin.hooks and not plugin.hooks.is_empty():
                    all_plugin_hooks.append(plugin.hooks)

                # Collect agent definitions
                if plugin.agents:
                    all_plugin_agents.extend(plugin.agents)

            logger.info(f"Loaded {len(self._plugin_specs)} plugin(s) via Conversation")

        # Resolve project skills from the workspace. AgentContext can't do this
        # itself (the workspace path is unknown at validation time), so it is done
        # here, where the path is known. Project skills take precedence over
        # same-named skills already on the context.
        project_skills_loaded = False
        if merged_context is not None and merged_context.load_project_skills:
            # Best-effort: a failure to load project skills must not prevent the
            # conversation from starting. (load_available_skills already guards
            # the project source internally; this is belt-and-suspenders.)
            try:
                project_skills = load_available_skills(
                    work_dir=self.workspace.working_dir,
                    include_user=False,
                    include_project=True,
                    include_public=False,
                )
            except Exception:
                logger.warning(
                    "Failed to load project skills; continuing without them",
                    exc_info=True,
                )
                project_skills = {}
            if project_skills:
                # Project skills are authoritative over same-named context skills.
                merged_skills = merge_skills_by_name(
                    project_skills.values(), merged_context.skills
                )
                merged_context = merged_context.model_copy(
                    update={"skills": merged_skills}
                )
                project_skills_loaded = True

        # Expand MCP config variables with per-conversation secrets
        # This handles ${VAR} and ${VAR:-default} placeholders:
        # - Variables referencing secrets injected via API are expanded to secret values
        # - Variables with defaults that don't have secrets fall back to their defaults
        # - This is the ONLY place where defaults are applied (plugin loading preserves
        #   placeholders with expand_defaults=False to avoid double-expansion)
        if merged_mcp:
            # Pass the registry's lookup method as a callback - secrets are retrieved
            # lazily, one at a time, only when actually referenced in the config
            merged_mcp = expand_mcp_variables(
                merged_mcp,
                {},
                get_secret=self._state.secret_registry.get_secret_value,
                expand_defaults=True,
            )
            logger.debug("Expanded MCP config variables")

        # Update agent with merged content only if something changed.
        # Skip update otherwise to avoid unnecessary agent state mutations.
        if self._plugin_specs or has_mcp_config or project_skills_loaded:
            self.agent = self.agent.model_copy(
                update={
                    "agent_context": merged_context,
                    "mcp_config": merged_mcp,
                }
            )

            # Also update the agent in _state so API responses reflect loaded plugins
            with self._state:
                self._state.agent = self.agent

        # Register file-based agents defined in plugins
        if all_plugin_agents:
            register_plugin_agents(
                agents=all_plugin_agents,
                work_dir=self.workspace.working_dir,
            )

        # Combine explicit hook_config with plugin hooks
        # Explicit hooks run first (before plugin hooks)
        final_hook_config = self._pending_hook_config
        if all_plugin_hooks:
            plugin_hooks = HookConfig.merge(all_plugin_hooks)
            if plugin_hooks is not None:
                if final_hook_config is not None:
                    final_hook_config = HookConfig.merge(
                        [final_hook_config, plugin_hooks]
                    )
                else:
                    final_hook_config = plugin_hooks

        # Set up hook processor with the combined config
        if final_hook_config is not None:
            # Store final hook_config in state for observability
            self._state.hook_config = final_hook_config
            hook_persistence_dir = (
                str(Path(self._state.persistence_dir).parent)
                if self._state.persistence_dir is not None
                else None
            )

            self._hook_processor, self._on_event = create_hook_callback(
                hook_config=final_hook_config,
                working_dir=str(self.workspace.working_dir),
                session_id=str(self._state.id),
                original_callback=self._base_callback,
                # Resolve lazily: switch_llm()/switch_profile() rebind self.agent,
                # so agent hooks must read the current LLM at execution time.
                llm_getter=lambda: self.agent.llm,
                persistence_dir=hook_persistence_dir,
                visualizer=self._visualizer,
                conversation_stats=self._state.stats,
            )
            self._hook_processor.set_conversation_state(self._state)
            self._hook_processor.run_session_start()

        self._plugins_loaded = True

    def _register_file_based_agents(self) -> None:
        """Discover and register file-based agents into the agent registry.

        Agents are loaded from Markdown definition files and registered via
        `register_agent_if_absent`, so they never overwrite agents that were
        already registered programmatically or by plugins.

        Registration order (highest to lowest priority):
          1. Programmatic `register_agent()` calls (already in the registry)
          2. Plugin agents (registered during plugin loading, i.e.,
                in _ensure_plugins_loaded())
          3. Project-level file agents (`{project}/.agents/agents/*.md`,
                then `{project}/.openhands/agents/*.md`)
          4. User-level file agents (`~/.agents/agents/*.md`,
                then `~/.openhands/agents/*.md`)
        """
        # register project-level and then user-level file-based agents
        register_file_agents(self.workspace.working_dir)

    def _ensure_agent_ready(self) -> None:
        """Ensure the agent is fully initialized with plugins and agents loaded.

        Performs one-time lazy initialization on the first `send_message()`
        or `run()` call.  The steps executed (in order) are:

        1. Load plugins (merges skills, MCP config, and hooks).
        2. Register file-based agents into the agent registry.
        3. Initialize the agent with complete plugin config and hooks.
        4. Register LLMs in the LLM registry.

        This preserves the design principle that constructors should not perform
        I/O or error-prone operations, while eliminating double initialization.

        Thread-safe: uses a double-checked lock on the conversation state to
        prevent concurrent initialization.
        """
        # Fast path: if already initialized, skip lock acquisition entirely.
        # This is crucial for concurrent send_message() calls during run(),
        # which holds the state lock during agent.step(). Without this check,
        # send_message() would block waiting for the lock even though no
        # initialization is needed.
        if self._agent_ready:
            return

        with self._state:
            # Re-check after acquiring lock in case another thread initialized
            if self._agent_ready:
                return

            # Load plugins first (merges skills, MCP config, hooks)
            self._ensure_plugins_loaded()

            # register file-based agents
            self._register_file_based_agents()

            # Initialize agent with complete configuration
            self.agent.init_state(self._state, on_event=self._on_event)

            # Register LLMs in the registry (still holding lock).
            # `registered` is updated after each add so that duplicate usage_ids
            # within the same batch are silently skipped (first-write-wins),
            # preventing a ValueError when e.g. agent and condenser LLMs were
            # both serialised with usage_id="default".
            self.llm_registry.subscribe(self._state.stats.register_llm)
            registered = set(self.llm_registry.list_usage_ids())
            for llm in list(self.agent.get_all_llms()):
                if llm.usage_id not in registered:
                    self.llm_registry.add(llm)
                    registered.add(llm.usage_id)
                self._pin_session_affinity_header(llm)

            self._agent_ready = True

    def _should_initialize_agent_on_send_message(self) -> bool:
        """Return whether send_message() should eagerly initialize the agent.

        ACPAgent startup is substantially heavier than regular agent
        initialization because it launches and handshakes with an external ACP
        subprocess. Deferring that work to run() keeps send_message() fast and
        avoids HTTP client read timeouts on the remote conversation endpoint.
        """
        return not isinstance(self.agent, ACPAgent)

    def _pin_prompt_cache_key(self) -> None:
        # Pin the OpenAI prefix-cache shard to this conversation (#2904, #2918).
        # Skip if a key is already set: sub-agent LLMs inherit the parent's
        # via model_copy, and overwriting would put each sub-agent on its own
        # shard, defeating cross-sub-agent cache reuse on OpenAI models.
        if self.agent.llm._prompt_cache_key is None:
            self.agent.llm._prompt_cache_key = str(self._state.id)

    def _pin_session_affinity_header(self, llm: LLM) -> None:
        """Ensure *llm* carries ``x-litellm-session-id`` for routing affinity.

        Note: if a caller passes ``extra_headers`` as a kwarg directly to
        ``completion()``, ``select_chat_options`` skips ``llm.extra_headers``
        entirely — the same limitation that affects OpenRouter headers.
        """
        existing = llm.extra_headers or {}
        if "x-litellm-session-id" not in existing:
            llm.extra_headers = {
                "x-litellm-session-id": str(self._state.id),
                **existing,
            }

    def switch_llm(self, llm: LLM) -> None:
        """Swap the agent's LLM to the given object.

        The caller owns ``llm.usage_id``; it is the registry key. If an
        entry with that key already exists, the cached LLM is reused and
        the passed ``llm`` is dropped — matching the rest of the
        registry's "first-write-wins" contract.

        Args:
            llm: LLM to install on the agent.
        """
        try:
            new_llm = self.llm_registry.get(llm.usage_id)
        except KeyError:
            new_llm = llm
            self.llm_registry.add(new_llm)
        # A switch_llm tool runs on a worker thread while run()/arun() holds the
        # state lock across the agent step on another thread, blocked awaiting
        # this very tool. Re-acquiring _state here would deadlock, so skip it:
        # the run loop is parked and no other mutator can run, so the swap is
        # safe without the lock (#3485). Only skip when the lock is held by a
        # different thread (the run loop); when the caller already owns it (a
        # sync step, or the switch endpoint reentering on the event-loop thread)
        # acquire normally — FIFOLock is reentrant for the owning thread.
        skip_lock = self._step_holds_state_lock and not self._state.owned()
        lock = contextlib.nullcontext() if skip_lock else self._state
        with lock:
            self.agent = self.agent.model_copy(update={"llm": new_llm})
            self._state.agent = self.agent
            self._pin_prompt_cache_key()
            self._pin_session_affinity_header(new_llm)

    def switch_profile(self, profile_name: str) -> None:
        """Switch the agent's LLM to a profile loaded from disk.

        Loads the profile from :class:`LLMProfileStore` (cached in the
        registry under ``profile:{profile_name}`` after first load) and
        delegates the swap to :meth:`switch_llm`.

        Args:
            profile_name: Name of a profile previously saved via LLMProfileStore.

        Raises:
            FileNotFoundError: If the profile does not exist.
            ValueError: If the profile is corrupted or invalid.
        """
        usage_id = f"profile:{profile_name}"
        try:
            cached = self.llm_registry.get(usage_id)
        except KeyError:
            loaded = self._profile_store.load(profile_name, cipher=self._cipher)
            cached = loaded.model_copy(update={"usage_id": usage_id})
        self.switch_llm(cached)

    def switch_acp_model(self, model: str) -> None:
        """Switch the model on a running ACP conversation (mid-conversation).

        Unlike :meth:`switch_llm`, which swaps OpenHands' own LLM object, this
        issues a protocol-level ``session/set_model`` call to the ACP
        subprocess so the new model applies to subsequent turns of the *same*
        session, preserving conversation context. ``switch_llm`` would not
        affect an ACP conversation, since the subprocess owns its own model.

        Args:
            model: Provider-specific model id to switch to.

        Raises:
            ValueError: If the conversation's agent is not an :class:`ACPAgent`,
                or the provider does not support runtime model switching, or
                the ACP server rejects the switch.
            RuntimeError: If the ACP session is not yet initialized.
            TimeoutError: If the ACP server does not respond within
                ``acp_prompt_timeout`` seconds.
        """
        if not isinstance(self.agent, ACPAgent):
            raise ValueError(
                "switch_acp_model is only supported for ACP conversations."
            )
        with self._state:
            # Perform the live protocol switch first; if it fails we leave the
            # persisted state untouched.
            self.agent.set_acp_model(model)
            # Persist the switched model as the authoritative value. ``acp_model``
            # is frozen, so we replace the agent with a copy carrying the new
            # value. This matters on two counts the in-place mutation missed:
            #   1. A fresh object identity makes the autosave path actually
            #      write base_state.json (re-assigning the same object is a
            #      no-op because old == new).
            #   2. model_post_init / _start_acp_server derive the sentinel model
            #      and the resumed session model from ``acp_model`` on reload, so
            #      it must hold the switched value, not the construction-time one.
            #
            # model_copy is shallow, so the copy shares the live ACP runtime
            # (_conn/_executor/_process) with the old agent. Disarm the old
            # agent's finalizer before dropping it: otherwise ACPAgent.__del__
            # -> close() on the discarded agent would tear down the session the
            # copy now owns, leaving the next turn pointing at a dead connection.
            old_agent = self.agent
            new_agent = old_agent.model_copy(update={"acp_model": model})
            old_agent.release_runtime()
            # ``self.agent`` is the live reference used by subsequent ``step()``
            # calls; ``self._state.agent`` is what the autosave path serializes
            # to base_state.json. Update both so the running conversation and the
            # persisted state agree on the switched model.
            self.agent = new_agent
            self._state.agent = new_agent
            # Keep the persisted model hint in sync with the switch. The live
            # agent's ``current_model_id`` (a PrivateAttr) already reflects the
            # new model and wins on warm reads, but cold list reads after a
            # process restart fall back to ``agent_state`` — which would
            # otherwise still name the pre-switch model until the next resume.
            # Write unconditionally: a successful switch is authoritative even
            # for an older/custom server that reported no ``models`` at init
            # (so the key may not exist yet).
            self._state.agent_state = {
                **self._state.agent_state,
                "acp_current_model_id": model,
            }

    @observe(name="conversation.send_message")
    def send_message(self, message: str | Message, sender: str | None = None) -> None:
        """Send a message to the agent.

        Args:
            message: Either a string (which will be converted to a user message)
                    or a Message object
            sender: Optional identifier of the sender. Can be used to track
                   message origin in multi-agent scenarios. For example, when
                   one agent delegates to another, the sender can be set to
                   identify which agent is sending the message.
        """
        # ACPAgent startup can take much longer than a normal send_message()
        # round-trip because it launches and initializes a subprocess-backed
        # session. Defer that work to run() so enqueueing the user message
        # remains fast for remote callers.
        if self._should_initialize_agent_on_send_message():
            self._ensure_agent_ready()

        if isinstance(message, str):
            message = Message(role="user", content=[TextContent(text=message)])

        assert message.role == "user", (
            "Only user messages are allowed to be sent to the agent."
        )
        with self._state:
            if self._state.execution_status in (
                ConversationExecutionStatus.FINISHED,
                ConversationExecutionStatus.STUCK,
            ):
                self._state.execution_status = (
                    ConversationExecutionStatus.IDLE
                )  # new message resets terminal states

            # TODO: We should add test cases for all these scenarios
            activated_skill_names: list[str] = []
            extended_content: list[TextContent] = []

            # Handle per-turn user message (i.e., knowledge agent trigger)
            if self.agent.agent_context:
                ctx = self.agent.agent_context.get_user_message_suffix(
                    user_message=message,
                    # We skip skills that were already activated
                    skip_skill_names=self._state.activated_knowledge_skills,
                )
                # TODO(calvin): we need to update
                # self._state.activated_knowledge_skills
                # so condenser can work
                if ctx:
                    content, activated_skill_names = ctx
                    logger.debug(
                        f"Got augmented user message content: {content}, "
                        f"activated skills: {activated_skill_names}"
                    )
                    extended_content.append(content)
                    self._state.activated_knowledge_skills.extend(activated_skill_names)

            user_msg_event = MessageEvent(
                source="user",
                llm_message=message,
                activated_skills=activated_skill_names,
                extended_content=extended_content,
                sender=sender,
            )
            self._on_event(user_msg_event)

    def _on_event_with_state_lock(self, event: Event) -> None:
        """Emit an event while holding the conversation state lock."""
        with self._state:
            self._on_event(event)

    @observe(name="conversation.run")
    def run(self) -> None:
        """Runs the conversation until the agent finishes.

        In confirmation mode:
        - First call: creates actions but doesn't execute them, stops and waits
        - Second call: executes pending actions (implicit confirmation)

        In normal mode:
        - Creates and executes actions immediately

        Can be paused between steps
        """
        # Ensure agent is fully initialized (loads plugins and initializes agent)
        self._ensure_agent_ready()
        self._cancel_token = CancellationToken()

        with self._state:
            if self._state.execution_status in [
                ConversationExecutionStatus.IDLE,
                ConversationExecutionStatus.PAUSED,
                ConversationExecutionStatus.ERROR,
                ConversationExecutionStatus.STUCK,
            ]:
                self._state.execution_status = ConversationExecutionStatus.RUNNING

        iteration = 0
        try:
            while True:
                logger.debug(f"Conversation run iteration {iteration}")
                with self._state:
                    # Pause attempts to acquire the state lock
                    # Before value can be modified step can be taken
                    # Ensure step conditions are checked when lock is already acquired
                    if self._state.execution_status in [
                        ConversationExecutionStatus.PAUSED,
                        ConversationExecutionStatus.STUCK,
                    ]:
                        break

                    # Handle stop hooks on FINISHED
                    if (
                        self._state.execution_status
                        == ConversationExecutionStatus.FINISHED
                    ):
                        if self._hook_processor is not None:
                            should_stop, feedback = self._hook_processor.run_stop(
                                reason="agent_finished"
                            )
                            if not should_stop:
                                logger.info("Stop hook denied agent stopping")
                                if feedback:
                                    prefixed = (
                                        f"{ACP_STOP_HOOK_FEEDBACK_PREFIX} {feedback}"
                                    )
                                    feedback_msg = MessageEvent(
                                        source="environment",
                                        llm_message=Message(
                                            role="user",
                                            content=[TextContent(text=prefixed)],
                                        ),
                                    )
                                    self._on_event(feedback_msg)
                                self._state.execution_status = (
                                    ConversationExecutionStatus.RUNNING
                                )
                                continue
                        # No hooks or hooks allowed stopping
                        break

                    # Check for stuck patterns if enabled
                    if self._stuck_detector:
                        is_stuck = self._stuck_detector.is_stuck()

                        if is_stuck:
                            logger.warning("Stuck pattern detected.")
                            self._state.execution_status = (
                                ConversationExecutionStatus.STUCK
                            )
                            continue

                    # clear the flag before calling agent.step() (user approved)
                    if (
                        self._state.execution_status
                        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
                    ):
                        self._state.execution_status = (
                            ConversationExecutionStatus.RUNNING
                        )

                    # Mark the step as holding the state lock so state-mutating
                    # tools (e.g. switch_llm) running on worker threads skip
                    # re-acquiring it instead of deadlocking (#3485).
                    self._step_holds_state_lock = True
                    try:
                        self.agent.step(
                            self, on_event=self._on_event, on_token=self._on_token
                        )
                    finally:
                        self._step_holds_state_lock = False
                    iteration += 1

                    # Check for non-finished terminal conditions
                    # Note: We intentionally do NOT check for FINISHED status here.
                    # This allows concurrent user messages to be processed:
                    # 1. Agent finishes and sets status to FINISHED
                    # 2. User sends message concurrently via send_message()
                    # 3. send_message() waits for FIFO lock, then sets status to IDLE
                    # 4. Run loop continues to next iteration and processes the message
                    # 5. Without this design, concurrent messages would be lost
                    if (
                        self.state.execution_status
                        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
                    ):
                        break

                    if iteration >= self.max_iteration_per_run:
                        # If the agent finished on this final iteration,
                        # preserve the FINISHED status rather than
                        # overwriting it with ERROR.
                        if (
                            self._state.execution_status
                            == ConversationExecutionStatus.FINISHED
                        ):
                            break
                        error_msg = (
                            f"Agent reached maximum iterations limit "
                            f"({self.max_iteration_per_run})."
                        )
                        logger.error(error_msg)
                        self._state.execution_status = ConversationExecutionStatus.ERROR
                        self._on_event(
                            ConversationErrorEvent(
                                source="environment",
                                code="MaxIterationsReached",
                                detail=error_msg,
                            )
                        )
                        break
        except Exception as e:
            with self._state:
                self._state.execution_status = ConversationExecutionStatus.ERROR

                # Add an error event
                self._on_event(
                    ConversationErrorEvent(
                        source="environment",
                        code=e.__class__.__name__,
                        detail=str(e),
                    )
                )

            # Re-raise with conversation id and persistence dir for better UX
            raise ConversationRunError(
                self._state.id, e, persistence_dir=self._state.persistence_dir
            ) from e
        finally:
            self._cancel_token = None

    @observe(name="conversation.arun")
    async def arun(self) -> None:
        """Async variant of :meth:`run`.

        Uses ``agent.astep()`` for non-blocking LLM I/O while keeping the
        same control-flow semantics (confirmation mode, stuck detection,
        stop hooks, iteration cap).

        The running task is tracked in ``_arun_task`` so that
        :meth:`interrupt` can cancel it mid-LLM-call.  On
        ``CancelledError`` the conversation transitions to ``PAUSED``
        and emits an :class:`InterruptEvent`.

        A fresh :class:`CancellationToken` is created per run so that
        ``interrupt()`` can signal in-flight tool calls to abort.  After
        ``CancelledError`` any ``ActionEvent`` without a matching
        observation is patched with a synthetic ``AgentErrorEvent`` so
        the LLM conversation history stays consistent.
        """
        self._arun_task = asyncio.current_task()
        self._cancel_token = CancellationToken()
        # Off-load lazy init to a worker thread: init_state may block the loop
        # (an ACP agent resolves credentials via a synchronous LookupSecret
        # httpx.get). When the agent-server runs arun() on its event loop and
        # that lookup points back at the same single-process server, doing it
        # inline freezes the loop so the lookup can never be served — a
        # self-deadlock that ReadTimeouts after 30s (agent-canvas#1072).
        # _ensure_agent_ready is thread-safe and already runs off-loop in run().
        await asyncio.to_thread(self._ensure_agent_ready)

        with self._state:
            if isinstance(self.agent, ACPAgent) and self._state.execution_status in (
                ConversationExecutionStatus.FINISHED,
                ConversationExecutionStatus.IDLE,
            ):
                updated_agent_state = dict(self._state.agent_state)
                inflight_prompt_user_message_id = updated_agent_state.get(
                    ACP_INFLIGHT_PROMPT_USER_MESSAGE_ID
                )
                if inflight_prompt_user_message_id is not None:
                    updated_agent_state[ACP_LAST_PROMPT_USER_MESSAGE_ID] = (
                        inflight_prompt_user_message_id
                    )
                    updated_agent_state.pop(ACP_INFLIGHT_PROMPT_USER_MESSAGE_ID, None)
                    self._state.agent_state = updated_agent_state

            if self._state.execution_status in [
                ConversationExecutionStatus.IDLE,
                ConversationExecutionStatus.PAUSED,
                ConversationExecutionStatus.ERROR,
                ConversationExecutionStatus.STUCK,
            ]:
                self._state.execution_status = ConversationExecutionStatus.RUNNING
            last_acp_prompt_user_message_id = self._state.agent_state.get(
                ACP_LAST_PROMPT_USER_MESSAGE_ID
            )

        iteration = 0
        try:
            while True:
                logger.debug(f"Conversation arun iteration {iteration}")
                acp_step_user_message_id: str | None = None
                acp_step_user_message: MessageEvent | None = None
                with self._state:
                    if self._state.execution_status in [
                        ConversationExecutionStatus.PAUSED,
                        ConversationExecutionStatus.STUCK,
                    ]:
                        break

                    if (
                        self._state.execution_status
                        == ConversationExecutionStatus.FINISHED
                    ):
                        if self._hook_processor is not None:
                            should_stop, feedback = self._hook_processor.run_stop(
                                reason="agent_finished"
                            )
                            if not should_stop:
                                logger.info("Stop hook denied agent stopping")
                                if feedback:
                                    prefixed = (
                                        f"{ACP_STOP_HOOK_FEEDBACK_PREFIX} {feedback}"
                                    )
                                    feedback_msg = MessageEvent(
                                        source="environment",
                                        llm_message=Message(
                                            role="user",
                                            content=[TextContent(text=prefixed)],
                                        ),
                                    )
                                    self._on_event(feedback_msg)
                                self._state.execution_status = (
                                    ConversationExecutionStatus.RUNNING
                                )
                                continue
                        break

                    if self._stuck_detector:
                        is_stuck = self._stuck_detector.is_stuck()
                        if is_stuck:
                            logger.warning("Stuck pattern detected.")
                            self._state.execution_status = (
                                ConversationExecutionStatus.STUCK
                            )
                            continue

                    if (
                        self._state.execution_status
                        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
                    ):
                        self._state.execution_status = (
                            ConversationExecutionStatus.RUNNING
                        )

                    if isinstance(self.agent, ACPAgent):
                        # Re-scan prompt messages under the lock each time we need
                        # the latest tail; the list is usually tiny, and correctness
                        # is more important than caching stale prompt snapshots.

                        acp_prompt_messages = [
                            event
                            for event in self._state.events
                            if _is_acp_prompt_message(event)
                        ]
                        if last_acp_prompt_user_message_id is None:
                            acp_step_user_message = (
                                acp_prompt_messages[0] if acp_prompt_messages else None
                            )
                        else:
                            last_prompt_index = next(
                                (
                                    index
                                    for index, event in enumerate(acp_prompt_messages)
                                    if event.id == last_acp_prompt_user_message_id
                                ),
                                None,
                            )
                            if last_prompt_index is None:
                                logger.info(
                                    "ACP prompt cursor %s no longer exists; "
                                    "restarting from first available prompt",
                                    last_acp_prompt_user_message_id,
                                )
                                acp_step_user_message = (
                                    acp_prompt_messages[0]
                                    if acp_prompt_messages
                                    else None
                                )
                            else:
                                acp_step_user_message = (
                                    acp_prompt_messages[last_prompt_index + 1]
                                    if last_prompt_index + 1 < len(acp_prompt_messages)
                                    else None
                                )
                        acp_step_user_message_id = (
                            acp_step_user_message.id
                            if acp_step_user_message is not None
                            else None
                        )
                    else:
                        # The state lock is held across this await. Mutations
                        # performed inside astep() (including native async
                        # Agent.astep / ACPAgent.astep that mutate on the
                        # event-loop thread) are intentional and part of this
                        # critical section. The invariant is only that no
                        # *unrelated* state mutator may run concurrently while
                        # the lock is held: because FIFOLock is thread- (not
                        # task-) reentrant, any unrelated state-mutating
                        # coroutine awaited on this event-loop thread would
                        # silently re-enter the lock and corrupt history. Such
                        # unrelated mutators must be dispatched via
                        # run_in_executor onto a worker thread.
                        # Mark the step as holding the state lock so
                        # state-mutating tools (e.g. switch_llm) running on
                        # worker threads skip re-acquiring it instead of
                        # deadlocking while this await holds it (#3485).
                        self._step_holds_state_lock = True
                        try:
                            await self.agent.astep(
                                self,
                                on_event=self._on_event,
                                on_token=self._on_token,
                            )
                        finally:
                            self._step_holds_state_lock = False
                        iteration += 1

                        if (
                            self.state.execution_status
                            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
                        ):
                            break

                        if iteration >= self.max_iteration_per_run:
                            if (
                                self._state.execution_status
                                == ConversationExecutionStatus.FINISHED
                            ):
                                break
                            error_msg = (
                                f"Agent reached maximum iterations limit "
                                f"({self.max_iteration_per_run})."
                            )
                            logger.error(error_msg)
                            self._state.execution_status = (
                                ConversationExecutionStatus.ERROR
                            )
                            self._on_event(
                                ConversationErrorEvent(
                                    source="environment",
                                    code="MaxIterationsReached",
                                    detail=error_msg,
                                )
                            )
                            break

                        continue

                # ACP prompt round-trips can run for minutes. Keep the state
                # lock free while awaiting them so incoming user messages can
                # be persisted immediately; event callbacks take the lock only
                # for each individual mutation.
                if acp_step_user_message is None:
                    with self._state:
                        acp_prompt_messages = [
                            event
                            for event in self._state.events
                            if _is_acp_prompt_message(event)
                        ]
                        latest_acp_prompt_message_id = (
                            acp_prompt_messages[-1].id if acp_prompt_messages else None
                        )
                        acp_prompt_message_changed = (
                            latest_acp_prompt_message_id is not None
                            and latest_acp_prompt_message_id
                            != last_acp_prompt_user_message_id
                        )
                        if acp_prompt_message_changed:
                            if iteration >= self.max_iteration_per_run:
                                logger.info(
                                    "User message arrived before ACP finish; "
                                    "leaving conversation idle for a follow-up run"
                                )
                                self._state.execution_status = (
                                    ConversationExecutionStatus.IDLE
                                )
                                break
                            logger.info(
                                "User message arrived before ACP finish; continuing run"
                            )
                            self._state.execution_status = (
                                ConversationExecutionStatus.RUNNING
                            )
                            continue
                        self._state.execution_status = (
                            ConversationExecutionStatus.FINISHED
                        )
                    break

                acp_step_start_event_count = 0
                with self._state:
                    if self._state.execution_status in (
                        ConversationExecutionStatus.PAUSED,
                        ConversationExecutionStatus.STUCK,
                    ):
                        break
                    acp_step_start_event_count = len(self._state.events)
                    if acp_step_user_message_id is not None:
                        self._state.agent_state = {
                            **self._state.agent_state,
                            ACP_INFLIGHT_PROMPT_USER_MESSAGE_ID: (
                                acp_step_user_message_id
                            ),
                        }

                await self.agent.astep(
                    self,
                    on_event=self._on_event_with_state_lock,
                    on_token=self._on_token,
                    prompt_message=acp_step_user_message,
                )
                with self._state:
                    iteration += 1
                    pause_requested_during_acp_step = any(
                        isinstance(event, PauseEvent)
                        for event in self._state.events[acp_step_start_event_count:]
                    )
                    updated_agent_state = dict(self._state.agent_state)
                    if (
                        updated_agent_state.get(ACP_INFLIGHT_PROMPT_USER_MESSAGE_ID)
                        == acp_step_user_message_id
                    ):
                        updated_agent_state.pop(
                            ACP_INFLIGHT_PROMPT_USER_MESSAGE_ID, None
                        )
                    updated_agent_state.pop(ACP_SUPERSEDE_INFLIGHT_PROMPT, None)
                    if (
                        acp_step_user_message_id is not None
                        and self._state.execution_status
                        not in (
                            ConversationExecutionStatus.ERROR,
                            ConversationExecutionStatus.STUCK,
                            ConversationExecutionStatus.PAUSED,
                        )
                    ):
                        last_acp_prompt_user_message_id = acp_step_user_message_id
                        updated_agent_state[ACP_LAST_PROMPT_USER_MESSAGE_ID] = (
                            acp_step_user_message_id
                        )
                    self._state.agent_state = updated_agent_state

                    if self._state.execution_status in (
                        ConversationExecutionStatus.ERROR,
                        ConversationExecutionStatus.STUCK,
                    ):
                        break
                    if pause_requested_during_acp_step:
                        self._state.execution_status = (
                            ConversationExecutionStatus.PAUSED
                        )
                        break

                    acp_prompt_messages = [
                        event
                        for event in self._state.events
                        if _is_acp_prompt_message(event)
                    ]
                    latest_acp_prompt_message_id = (
                        acp_prompt_messages[-1].id if acp_prompt_messages else None
                    )
                    acp_prompt_message_changed = (
                        latest_acp_prompt_message_id is not None
                        and latest_acp_prompt_message_id
                        != last_acp_prompt_user_message_id
                    )
                    if acp_prompt_message_changed and self._state.execution_status in (
                        ConversationExecutionStatus.FINISHED,
                        ConversationExecutionStatus.IDLE,
                    ):
                        if iteration >= self.max_iteration_per_run:
                            logger.info(
                                "User message arrived during final ACP iteration; "
                                "leaving conversation idle for a follow-up run"
                            )
                            self._state.execution_status = (
                                ConversationExecutionStatus.IDLE
                            )
                            break
                        logger.info(
                            "User message arrived during ACP step; continuing run"
                        )
                        self._state.execution_status = (
                            ConversationExecutionStatus.RUNNING
                        )

                    if (
                        self.state.execution_status
                        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
                    ):
                        break

                    if iteration >= self.max_iteration_per_run:
                        if (
                            self._state.execution_status
                            == ConversationExecutionStatus.FINISHED
                        ):
                            break
                        error_msg = (
                            f"Agent reached maximum iterations limit "
                            f"({self.max_iteration_per_run})."
                        )
                        logger.error(error_msg)
                        self._state.execution_status = ConversationExecutionStatus.ERROR
                        self._on_event(
                            ConversationErrorEvent(
                                source="environment",
                                code="MaxIterationsReached",
                                detail=error_msg,
                            )
                        )
                        break
        except asyncio.CancelledError:
            # CancelledError is intentionally NOT re-raised.  ``interrupt()``
            # uses ``asyncio.Task.cancel()`` to break out of ``arun()`` and
            # expects the task to terminate cleanly.  Re-raising would
            # propagate the cancellation to EventService/caller which would
            # surface it as an unexpected error.  Instead we transition to
            # PAUSED so the conversation can be resumed later.
            logger.info("arun() interrupted via task cancellation")
            with self._state:
                updated_agent_state = dict(self._state.agent_state)
                inflight_prompt_user_message_id = updated_agent_state.pop(
                    ACP_INFLIGHT_PROMPT_USER_MESSAGE_ID, None
                )
                superseded_by_new_message = bool(
                    updated_agent_state.pop(ACP_SUPERSEDE_INFLIGHT_PROMPT, False)
                )
                completed_cancelled_prompt = (
                    self._state.execution_status == ConversationExecutionStatus.FINISHED
                )
                if (
                    superseded_by_new_message or completed_cancelled_prompt
                ) and inflight_prompt_user_message_id is not None:
                    updated_agent_state[ACP_LAST_PROMPT_USER_MESSAGE_ID] = (
                        inflight_prompt_user_message_id
                    )
                self._state.agent_state = updated_agent_state

                # Emit synthetic error observations for any ActionEvents
                # that were in-flight when the interrupt landed.  Without
                # these the LLM history would contain tool-call requests
                # with no tool-result, which causes provider errors on
                # the next completion call.
                self._emit_orphaned_action_errors()

                self._state.execution_status = ConversationExecutionStatus.PAUSED
                self._on_event(InterruptEvent())
        except Exception as e:
            with self._state:
                updated_agent_state = dict(self._state.agent_state)
                updated_agent_state.pop(ACP_INFLIGHT_PROMPT_USER_MESSAGE_ID, None)
                updated_agent_state.pop(ACP_SUPERSEDE_INFLIGHT_PROMPT, None)
                self._state.agent_state = updated_agent_state
                self._state.execution_status = ConversationExecutionStatus.ERROR
                self._on_event(
                    ConversationErrorEvent(
                        source="environment",
                        code=e.__class__.__name__,
                        detail=str(e),
                    )
                )
            raise ConversationRunError(
                self._state.id, e, persistence_dir=self._state.persistence_dir
            ) from e
        finally:
            # A cancelled token must stay observable: interrupted tool calls run
            # in worker threads that can outlive arun() and still poll it. A
            # fresh token is created on the next run().
            if self._cancel_token is not None and not self._cancel_token.is_cancelled:
                self._cancel_token = None
            self._arun_task = None

    def set_confirmation_policy(self, policy: ConfirmationPolicyBase) -> None:
        """Set the confirmation policy and store it in conversation state."""
        with self._state:
            self._state.confirmation_policy = policy
        logger.info(f"Confirmation policy set to: {policy}")

    def reject_pending_actions(self, reason: str = "User rejected the action") -> None:
        """Reject all pending actions from the agent.

        This is a non-invasive method to reject actions between run() calls.
        Also clears the agent_waiting_for_confirmation flag.
        """
        pending_actions = ConversationState.get_unmatched_actions(self._state.events)

        with self._state:
            # Always clear the agent_waiting_for_confirmation flag
            if (
                self._state.execution_status
                == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
            ):
                self._state.execution_status = ConversationExecutionStatus.IDLE

            if not pending_actions:
                logger.warning("No pending actions to reject")
                return

            for action_event in pending_actions:
                # Create rejection observation
                rejection_event = UserRejectObservation(
                    action_id=action_event.id,
                    tool_name=action_event.tool_name,
                    tool_call_id=action_event.tool_call_id,
                    rejection_reason=reason,
                )
                self._on_event(rejection_event)
                logger.info(f"Rejected pending action: {action_event} - {reason}")

    def _emit_orphaned_action_errors(self) -> None:
        """Emit ``AgentErrorEvent`` for actions that have no observation.

        After an interrupt, tool calls that were in-flight may have their
        ``ActionEvent`` already in the history but no corresponding
        ``ObservationEvent``.  LLM providers reject conversation
        histories with orphaned tool-call requests, so we backfill
        them with a synthetic error.

        Must be called while holding ``self._state``.
        """
        orphans = ConversationState.get_unmatched_actions(self._state.events)
        for ae in orphans:
            logger.info(
                "Emitting synthetic error for orphaned action %s (%s)",
                ae.id,
                ae.tool_name,
            )
            self._on_event(
                AgentErrorEvent(
                    error=(
                        "Tool call interrupted before completion. "
                        "The conversation was paused."
                    ),
                    tool_name=ae.tool_name,
                    tool_call_id=ae.tool_call_id,
                )
            )

    def pause(self) -> None:
        """Pause agent execution.

        This method can be called from any thread to request that the agent
        pause execution. The pause will take effect at the next iteration
        of the run loop (between agent steps).

        Note: If called during an LLM completion, the pause will not take
        effect until the current LLM call completes.
        """

        if self._state.execution_status == ConversationExecutionStatus.PAUSED:
            return

        with self._state:
            # Only pause when running or idle
            if (
                self._state.execution_status == ConversationExecutionStatus.IDLE
                or self._state.execution_status == ConversationExecutionStatus.RUNNING
            ):
                self._state.execution_status = ConversationExecutionStatus.PAUSED
                pause_event = PauseEvent()
                self._on_event(pause_event)
                logger.info("Agent execution pause requested")

    def interrupt(self) -> None:
        """Immediately cancel an in-flight ``arun()``, including mid-LLM-call.

        If an async run is in progress the underlying ``asyncio.Task`` is
        cancelled; ``arun()`` catches the resulting ``CancelledError``, sets
        execution status to ``PAUSED``, and emits an
        :class:`~openhands.sdk.event.InterruptEvent`.

        The cancellation token is set *before* cancelling the task so
        that :class:`ParallelToolExecutor` can skip pending tool calls
        and individual tools can check for early exit.

        If no async task is tracked (e.g. the synchronous ``run()`` is active)
        the call falls back to :meth:`pause`.

        This method is safe to call from signal handlers and from other
        threads (the cancellation is scheduled on the task's event loop).
        """
        # Set the cancellation token first so thread-pool workers see it
        # before the asyncio task is cancelled.
        token = self._cancel_token
        if token is not None:
            token.cancel()

        task = self._arun_task
        if task is not None and not task.done():
            # Marshal cancellation onto the task's event loop so this is
            # safe to call from any thread (e.g. signal handlers, the
            # agent-server's HTTP thread).
            loop = task.get_loop()
            loop.call_soon_threadsafe(task.cancel)
            logger.info("interrupt(): cancelled in-flight arun() task")
        else:
            self.pause()

    def update_secrets(self, secrets: Mapping[str, SecretValue]) -> None:
        """Add secrets to the conversation's secret registry.

        Secrets are stored in the conversation's secret_registry which:
        1. Provides environment variable injection during command execution
        2. Is read by the agent when building its system prompt (dynamic_context)

        The agent pulls secrets from the registry via get_dynamic_context() during
        init_state(), ensuring secret names and descriptions appear in the prompt.

        Args:
            secrets: Dictionary mapping secret keys to values or no-arg callables.
                     SecretValue = str | Callable[[], str]. Callables are invoked lazily
                     when a command references the secret key.
        """
        secret_registry = self._state.secret_registry
        secret_registry.update_secrets(secrets)
        logger.info(f"Added {len(secrets)} secrets to conversation")

    def set_security_analyzer(self, analyzer: SecurityAnalyzerBase | None) -> None:
        """Set the security analyzer for the conversation."""
        with self._state:
            self._state.security_analyzer = analyzer

    def close(self) -> None:
        """Close the conversation and clean up all tool executors."""
        # Remove the atexit reference so the conversation object can be GC'd
        # after close. atexit.unregister is a no-op if not registered.
        atexit.unregister(self.close)
        # Use getattr for safety - object may be partially constructed
        if getattr(self, "_cleanup_initiated", False):
            return
        self._cleanup_initiated = True
        logger.debug("Closing conversation and cleaning up tool executors")
        hook_processor = getattr(self, "_hook_processor", None)
        if hook_processor is not None:
            hook_processor.run_session_end()
        try:
            self._end_observability_span()
        except AttributeError:
            # Object may be partially constructed; span fields may be missing.
            pass
        # Clean up agent resources (e.g., ACPAgent subprocess)
        try:
            self.agent.close()
        except Exception as e:
            logger.warning(f"Error closing agent: {e}")
        # Always close tool executors — they hold runtime resources
        # (subprocesses, connections, etc.) that must be released regardless
        # of whether the conversation data is preserved (delete_on_close).
        with contextlib.suppress(AttributeError, RuntimeError):
            # Agent not initialized or partially constructed → skip
            for tool in self.agent.tools_map.values():
                with contextlib.suppress(NotImplementedError):
                    try:
                        executable_tool = tool.as_executable()
                        executable_tool.executor.close()
                    except Exception as e:
                        logger.warning(
                            f"Error closing executor for tool '{tool.name}': {e}"
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
        # Ensure agent is initialized (needs tools_map)
        self._ensure_agent_ready()

        # Try agent-specific override first (e.g. ACPAgent uses fork_session)
        agent_response = self.agent.ask_agent(question)
        if agent_response is not None:
            return agent_response

        # Import here to avoid circular imports
        from openhands.sdk.agent.utils import make_llm_completion, prepare_llm_messages

        template_dir = (
            Path(__file__).parent.parent.parent / "context" / "prompts" / "templates"
        )

        question_text = render_template(
            str(template_dir), "ask_agent_template.j2", question=question
        )

        # Create a user message with the context-aware question
        user_message = Message(
            role="user",
            content=[TextContent(text=question_text)],
        )

        messages = prepare_llm_messages(
            self.state.events, additional_messages=[user_message]
        )

        # Get or create the specialized ask-agent LLM
        try:
            question_llm = self.llm_registry.get("ask-agent-llm")
        except KeyError:
            question_llm = self.agent.llm.model_copy(
                update={
                    "usage_id": "ask-agent-llm",
                },
                deep=True,
            )
            self.llm_registry.add(question_llm)

        # Pass agent tools so LLM can understand tool_calls in conversation history
        response = make_llm_completion(
            question_llm, messages, tools=list(self.agent.tools_map.values())
        )

        message = response.message

        # Extract the text content from the LLMResponse message
        if message.content and len(message.content) > 0:
            # Look for the first TextContent in the response
            for content in response.message.content:
                if isinstance(content, TextContent):
                    return content.text

        raise Exception("Failed to generate summary")

    @observe(name="conversation.generate_title", ignore_inputs=["llm"])
    def generate_title(self, llm: LLM | None = None, max_length: int = 50) -> str:
        """Generate a title for the conversation based on the first user message.

        If an explicit LLM is provided, it takes precedence. Otherwise the
        agent's LLM is used. If neither is available, the title falls back to
        simple message truncation.

        Args:
            llm: Optional LLM to use for title generation. Takes precedence
                 over the agent's LLM when provided.
            max_length: Maximum length of the generated title.

        Returns:
            A generated title for the conversation.

        Raises:
            ValueError: If no user messages are found in the conversation.
        """
        effective_llm = llm if llm is not None else self.agent.llm
        return generate_conversation_title(
            events=self._state.events, llm=effective_llm, max_length=max_length
        )

    def condense(self) -> None:
        """Synchronously force condense the conversation history.

        If the agent is currently running, `condense()` will wait for the
        ongoing step to finish before proceeding.

        Raises ValueError if no compatible condenser exists.
        """

        # Check if condenser is configured and handles condensation requests
        if (
            self.agent.condenser is None
            or not self.agent.condenser.handles_condensation_requests()
        ):
            condenser_info = (
                "No condenser configured"
                if self.agent.condenser is None
                else (
                    f"Condenser {type(self.agent.condenser).__name__} does not handle "
                    "condensation requests"
                )
            )
            raise ValueError(
                f"Cannot condense conversation: {condenser_info}. "
                "To enable manual condensation, configure an "
                "LLMSummarizingCondenser:\n\n"
                "from openhands.sdk.context.condenser import LLMSummarizingCondenser\n"
                "agent = Agent(\n"
                "    llm=your_llm,\n"
                "    condenser=LLMSummarizingCondenser(\n"
                "        llm=your_llm,\n"
                "        max_size=120,\n"
                "        keep_first=4\n"
                "    )\n"
                ")"
            )

        # Add a condensation request event
        condensation_request = CondensationRequest()
        self._on_event(condensation_request)

        # Force the agent to take a single step to process the condensation request
        # This will trigger the condenser if it handles condensation requests
        with self._state:
            # Take a single step to process the condensation request
            self.agent.step(self, on_event=self._on_event, on_token=self._on_token)

        logger.info("Condensation request processed")

    def rerun_actions(
        self,
        rerun_log_path: str | Path | None = None,
    ) -> bool:
        """Re-execute all actions from the conversation's event history.

        This method iterates through all ActionEvents in the conversation and
        re-executes them using their original action parameters. Execution
        stops immediately if any tool call fails.

        WARNING: This is an advanced feature intended for specific use cases
        such as reproducing environment state from a saved conversation. Many
        tool operations are NOT idempotent:

        - File operations may fail if files already exist or were deleted
        - Terminal commands may have different effects on changed state
        - API calls may have side effects or return different results
        - Browser state may differ from the original session

        Use this method only when you understand that:
        1. Results may differ from the original conversation
        2. Some actions may fail due to changed environment state
        3. The workspace should typically be reset before rerunning

        Args:
            rerun_log_path: Optional directory path to save a rerun event log.
                If provided, events will be written incrementally to disk using
                EventLog, avoiding memory buildup for large conversations.

        Returns:
            True if all actions executed successfully, False if any action failed.

        Raises:
            KeyError: If a tool from the original conversation is not available.
                This is a configuration error (different from execution failure).
        """
        # Ensure agent is initialized (loads plugins and initializes tools)
        self._ensure_agent_ready()

        # Set up rerun log if path provided
        rerun_log: EventLog | None = None
        if rerun_log_path is not None:
            log_dir = Path(rerun_log_path)
            log_dir.mkdir(parents=True, exist_ok=True)
            file_store = LocalFileStore(str(log_dir))
            rerun_log = EventLog(file_store, dir_path="events")

        action_count = 0

        for event in self._state.events:
            if not isinstance(event, ActionEvent):
                continue
            if event.action is None:
                # Skip actions that failed validation during original run
                continue

            action_count += 1
            tool_name = event.tool_name

            # Get the tool from the agent's tools_map
            tool = self.agent.tools_map.get(tool_name)
            if tool is None:
                available_tools = list(self.agent.tools_map.keys())
                raise KeyError(
                    f"Tool '{tool_name}' not found during rerun. "
                    f"Available tools: {available_tools}. "
                    f"Ensure the agent is configured with the same tools as the "
                    f"original conversation."
                )

            if not tool.executor:
                logger.warning(
                    f"Skipping action {action_count}: "
                    f"tool '{tool_name}' has no executor"
                )
                continue

            # Execute the tool with the original action
            try:
                logger.info(f"Rerunning action {action_count}: {tool_name}")
                observation = tool(event.action, self)

                # Log the action and observation incrementally
                if rerun_log is not None:
                    # Append action event (copy from original)
                    rerun_log.append(event)
                    # Append observation event
                    obs_event = ObservationEvent(
                        source="environment",
                        tool_name=tool_name,
                        tool_call_id=event.tool_call_id,
                        observation=observation,
                        action_id=event.id,
                    )
                    rerun_log.append(obs_event)
            except Exception as e:
                logger.error(
                    f"Action {action_count} ({tool_name}) failed during rerun: {e}"
                )
                # Log is already written incrementally, just return failure
                return False

        logger.info(f"Rerun complete: {action_count} actions processed successfully")
        return True

    def execute_tool(self, tool_name: str, action: Action) -> Observation:
        """Execute a tool directly without going through the agent loop.

        This method allows executing tools before or outside of the normal
        conversation.run() flow. It handles agent initialization automatically,
        so tools can be executed before the first run() call.

        Note: This method bypasses the agent loop, including confirmation
        policies and security analyzer checks. Callers are responsible for
        applying any safeguards before executing potentially destructive tools.

        This is useful for:
        - Pre-run setup operations (e.g., indexing repositories)
        - Manual tool execution for environment setup
        - Testing tool behavior outside the agent loop

        Args:
            tool_name: The name of the tool to execute (e.g., "sleeptime_compute")
            action: The action to pass to the tool executor

        Returns:
            The observation returned by the tool execution

        Raises:
            KeyError: If the tool is not found in the agent's tools
            NotImplementedError: If the tool has no executor
        """
        # Ensure agent is initialized (loads plugins and initializes tools)
        self._ensure_agent_ready()

        # Get the tool from the agent's tools_map
        tool = self.agent.tools_map.get(tool_name)
        if tool is None:
            available_tools = list(self.agent.tools_map.keys())
            raise KeyError(
                f"Tool '{tool_name}' not found. Available tools: {available_tools}"
            )

        # Execute the tool
        if not tool.executor:
            raise NotImplementedError(f"Tool '{tool_name}' has no executor")
        return tool(action, self)

    def __del__(self) -> None:
        """Ensure cleanup happens when conversation is destroyed."""
        try:
            self.close()
        except Exception as e:
            logger.warning(f"Error during conversation cleanup: {e}", exc_info=True)
