import atexit
import uuid
from collections.abc import Mapping
from pathlib import Path

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context.prompts.prompt import render_template
from openhands.sdk.conversation.base import BaseConversation
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
    CondensationRequest,
    MessageEvent,
    PauseEvent,
    UserRejectObservation,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.hooks import HookConfig, HookEventProcessor, create_hook_callback
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.llm_registry import LLMRegistry
from openhands.sdk.logger import get_logger
from openhands.sdk.observability.laminar import observe
from openhands.sdk.plugin import (
    Plugin,
    PluginSource,
    ResolvedPluginSource,
    fetch_plugin_with_resolution,
)
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    ConfirmationPolicyBase,
)
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


logger = get_logger(__name__)


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
        """
        super().__init__()  # Initialize with span tracking
        # Mark cleanup as initiated as early as possible to avoid races or partially
        # initialized instances during interpreter shutdown.
        self._cleanup_initiated = False

        # Store plugin specs for lazy loading (no IO in constructor)
        # Plugins will be loaded on first run() or send_message() call
        self._plugin_specs = plugins
        self._resolved_plugins = None
        self._plugins_loaded = False
        self._pending_hook_config = hook_config  # Will be combined with plugin hooks
        self._agent_ready = False  # Agent initialized lazily after plugins loaded

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
        )

        # Default callback: persist every event to state
        def _default_callback(e):
            self._state.events.append(e)

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

        # Initialize secrets if provided
        if secrets:
            # Convert dict[str, str] to dict[str, SecretValue]
            secret_values: dict[str, SecretValue] = {k: v for k, v in secrets.items()}
            self.update_secrets(secret_values)

        atexit.register(self.close)
        self._start_observability_span(str(desired_id))
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
    def resolved_plugins(self) -> list[ResolvedPluginSource] | None:
        """Get the resolved plugin sources after plugins are loaded.

        Returns None if plugins haven't been loaded yet, or if no plugins
        were specified. Use this for persistence to ensure conversation
        resume uses the exact same plugin versions.
        """
        return self._resolved_plugins

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

        # Load plugins if specified
        if self._plugin_specs:
            logger.info(f"Loading {len(self._plugin_specs)} plugin(s)...")
            self._resolved_plugins = []

            # Start with agent's existing context and MCP config
            merged_context = self.agent.agent_context
            merged_mcp = dict(self.agent.mcp_config) if self.agent.mcp_config else {}

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

                # Collect hooks
                if plugin.hooks and not plugin.hooks.is_empty():
                    all_plugin_hooks.append(plugin.hooks)

            # Update agent with merged content
            self.agent = self.agent.model_copy(
                update={
                    "agent_context": merged_context,
                    "mcp_config": merged_mcp,
                }
            )

            # Also update the agent in _state so API responses reflect loaded plugins
            with self._state:
                self._state.agent = self.agent

            logger.info(f"Loaded {len(self._plugin_specs)} plugin(s) via Conversation")

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
            self._hook_processor, self._on_event = create_hook_callback(
                hook_config=final_hook_config,
                working_dir=str(self.workspace.working_dir),
                session_id=str(self._state.id),
                original_callback=self._base_callback,
            )
            self._hook_processor.set_conversation_state(self._state)
            self._hook_processor.run_session_start()

        self._plugins_loaded = True

    def _ensure_agent_ready(self) -> None:
        """Ensure agent is fully initialized with plugins loaded.

        This method combines plugin loading and agent initialization to ensure
        the agent is initialized exactly once with complete configuration.

        Called lazily on first send_message() or run() to:
        1. Load plugins (if specified)
        2. Initialize agent with complete plugin config and hooks
        3. Register LLMs in the registry

        This preserves the design principle that constructors should not perform
        I/O or error-prone operations, while eliminating double initialization.

        Thread-safe: Uses state lock to prevent concurrent initialization.
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

            # Initialize agent with complete configuration
            self.agent.init_state(self._state, on_event=self._on_event)

            # Register LLMs in the registry (still holding lock)
            self.llm_registry.subscribe(self._state.stats.register_llm)
            for llm in list(self.agent.get_all_llms()):
                self.llm_registry.add(llm)

            self._agent_ready = True

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
        # Ensure agent is fully initialized (loads plugins and initializes agent)
        self._ensure_agent_ready()

        # Convert string to Message if needed
        if isinstance(message, str):
            message = Message(role="user", content=[TextContent(text=message)])

        assert message.role == "user", (
            "Only user messages are allowed to be sent to the agent."
        )
        with self._state:
            if self._state.execution_status == ConversationExecutionStatus.FINISHED:
                self._state.execution_status = (
                    ConversationExecutionStatus.IDLE
                )  # now we have a new message

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

        with self._state:
            if self._state.execution_status in [
                ConversationExecutionStatus.IDLE,
                ConversationExecutionStatus.PAUSED,
                ConversationExecutionStatus.ERROR,
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
                                    prefixed = f"[Stop hook feedback] {feedback}"
                                    feedback_msg = MessageEvent(
                                        source="user",
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

                    self.agent.step(
                        self, on_event=self._on_event, on_token=self._on_token
                    )
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

    def update_secrets(self, secrets: Mapping[str, SecretValue]) -> None:
        """Add secrets to the conversation.

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
        if self.delete_on_close:
            try:
                tools_map = self.agent.tools_map
            except (AttributeError, RuntimeError):
                # Agent not initialized or partially constructed
                return
            for tool in tools_map.values():
                try:
                    executable_tool = tool.as_executable()
                    executable_tool.executor.close()
                except NotImplementedError:
                    # Tool has no executor, skip it without erroring
                    continue
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

        Args:
            llm: Optional LLM to use for title generation. If not provided,
                 uses self.agent.llm.
            max_length: Maximum length of the generated title.

        Returns:
            A generated title for the conversation.

        Raises:
            ValueError: If no user messages are found in the conversation.
        """
        # Use provided LLM or fall back to agent's LLM
        llm_to_use = llm or self.agent.llm

        return generate_conversation_title(
            events=self._state.events, llm=llm_to_use, max_length=max_length
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

    def __del__(self) -> None:
        """Ensure cleanup happens when conversation is destroyed."""
        try:
            self.close()
        except Exception as e:
            logger.warning(f"Error during conversation cleanup: {e}", exc_info=True)
