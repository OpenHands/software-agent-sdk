from __future__ import annotations

import json
import os
import re
import sys
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    SerializationInfo,
    ValidationInfo,
    field_serializer,
    model_serializer,
    model_validator,
)

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.condenser import CondenserBase
from openhands.sdk.context.prompts.prompt import render_template
from openhands.sdk.critic.base import CriticBase
from openhands.sdk.llm import LLM
from openhands.sdk.llm.utils.model_prompt_spec import get_model_prompt_spec
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.tool import (
    BUILT_IN_TOOL_CLASSES,
    BUILT_IN_TOOLS,
    Tool,
    ToolDefinition,
    resolve_tool,
)
from openhands.sdk.tool.builtins import InvokeSkillTool
from openhands.sdk.utils.models import DiscriminatedUnionMixin


if TYPE_CHECKING:
    from openhands.sdk.conversation import ConversationState, LocalConversation
    from openhands.sdk.conversation.types import (
        ConversationCallbackType,
        ConversationTokenCallbackType,
    )
    from openhands.sdk.utils.cipher import Cipher

logger = get_logger(__name__)


class AgentBase(DiscriminatedUnionMixin, ABC):
    """Abstract base class for OpenHands agents.

    Agents are stateless and should be fully defined by their configuration.
    This base class provides the common interface and functionality that all
    agent implementations must follow.
    """

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
    )

    llm: LLM = Field(
        ...,
        description="LLM configuration for the agent.",
        examples=[
            {
                "model": "litellm_proxy/anthropic/claude-sonnet-4-5-20250929",
                "base_url": "https://llm-proxy.eval.all-hands.dev",
                "api_key": "your_api_key_here",
            }
        ],
    )
    tools: list[Tool] = Field(
        default_factory=list,
        description="List of tools to initialize for the agent.",
        examples=[
            {"name": "TerminalTool", "params": {}},
            {"name": "FileEditorTool", "params": {}},
            {
                "name": "TaskTrackerTool",
                "params": {},
            },
        ],
    )
    mcp_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional MCP configuration dictionary to create MCP tools.",
        examples=[
            {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}
        ],
    )
    filter_tools_regex: str | None = Field(
        default=None,
        description="Optional regex to filter the tools available to the agent by name."
        " This is applied after any tools provided in `tools` and any MCP tools are"
        " added.",
        examples=["^(?!repomix)(.*)|^repomix.*pack_codebase.*$"],
    )
    include_default_tools: list[str] = Field(
        default_factory=lambda: [tool.__name__ for tool in BUILT_IN_TOOLS],
        description=(
            "List of default tool class names to include. By default, the agent "
            "includes 'FinishTool' and 'ThinkTool'. Set to an empty list to disable "
            "all default tools, or provide a subset to include only specific ones. "
            "Example: include_default_tools=['FinishTool'] to only include FinishTool, "
            "or include_default_tools=[] to disable all default tools."
        ),
        examples=[["FinishTool", "ThinkTool"], ["FinishTool"], []],
    )
    agent_context: AgentContext | None = Field(
        default=None,
        description="Optional AgentContext to initialize "
        "the agent with specific context.",
        examples=[
            {
                "skills": [
                    {
                        "name": "AGENTS.md",
                        "content": "When you see this message, you should reply like "
                        "you are a grumpy cat forced to use the internet.",
                        "type": "repo",
                    },
                    {
                        "name": "flarglebargle",
                        "content": (
                            "IMPORTANT! The user has said the magic word "
                            '"flarglebargle". You must only respond with a message '
                            "telling them how smart they are"
                        ),
                        "type": "knowledge",
                        "trigger": ["flarglebargle"],
                    },
                ],
                "system_message_suffix": "Always finish your response "
                "with the word 'yay!'",
                "user_message_prefix": "The first character of your "
                "response should be 'I'",
            }
        ],
    )
    system_prompt: str | None = Field(
        default=None,
        description=(
            "Inline system prompt string.  When provided, the agent uses this "
            "text verbatim as the system message instead of rendering from "
            "`system_prompt_filename`.  Mutually exclusive with a non-default "
            "`system_prompt_filename`.\n\n"
            "**Warning**: This is not recommended unless you know what you are "
            "doing (e.g. customising agent behaviour for a completely different "
            "task).  Setting this will override OpenHands' built-in system "
            "instructions that govern default agent behaviour."
        ),
    )
    system_prompt_filename: str = Field(
        default="system_prompt.j2",
        description=(
            "System prompt template filename. Can be either:\n"
            "- A relative filename (e.g., 'system_prompt.j2') loaded from the "
            "agent's prompts directory\n"
            "- An absolute path (e.g., '/path/to/custom_prompt.j2')"
        ),
    )
    security_policy_filename: str = Field(
        default="security_policy.j2",
        description=(
            "Security policy template filename. Can be either:\n"
            "- A relative filename (e.g., 'security_policy.j2') loaded from the "
            "agent's prompts directory\n"
            "- An absolute path (e.g., '/path/to/custom_security_policy.j2')\n"
            "- Empty string to disable security policy"
        ),
    )
    system_prompt_kwargs: dict[str, object] = Field(
        default_factory=dict,
        description="Optional kwargs to pass to the system prompt Jinja2 template.",
        examples=[{"cli_mode": True}],
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_system_prompt_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if (
            "security_policy_filename" in data
            and data["security_policy_filename"] is None
        ):
            data["security_policy_filename"] = ""
        has_inline = data.get("system_prompt") is not None
        has_custom_filename = (
            "system_prompt_filename" in data
            and data["system_prompt_filename"] != "system_prompt.j2"
        )
        if has_inline and has_custom_filename:
            raise ValueError(
                "Cannot set both 'system_prompt' and a non-default "
                "'system_prompt_filename'. Use one or the other."
            )
        return data

    @model_validator(mode="before")
    @classmethod
    def _decrypt_mcp_config(cls, data: Any, info: ValidationInfo) -> Any:
        """Decrypt encrypted_mcp_config if present and cipher is in context.

        Handles backward compatibility:
        - If encrypted_mcp_config exists and cipher is present: decrypt and
          set mcp_config
        - If mcp_config exists directly: use it as-is (plaintext or
          expose_secrets case)
        - If neither exists: default empty dict will be used
        """
        if not isinstance(data, dict):
            return data

        encrypted = data.pop("encrypted_mcp_config", None)
        if encrypted is None:
            return data

        # If no cipher in context, we can't decrypt - the encrypted value is lost
        if not info.context or not info.context.get("cipher"):
            logger.warning(
                "Found encrypted_mcp_config but no cipher in context - "
                "MCP configuration will be lost. Provide a cipher to preserve it."
            )
            return data

        cipher: Cipher = info.context["cipher"]
        decrypted = cipher.decrypt(encrypted)
        if decrypted is None:
            logger.warning(
                "Failed to decrypt mcp_config (cipher mismatch or corruption) - "
                "MCP configuration will be lost."
            )
            return data

        try:
            data["mcp_config"] = json.loads(decrypted.get_secret_value())
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse decrypted mcp_config as JSON: {e}")

        return data

    @field_serializer("mcp_config", when_used="always")
    def _serialize_mcp_config(self, v: dict[str, Any], info) -> dict[str, Any] | None:
        """Serialize mcp_config with encryption or redaction.

        - If a cipher is provided in context: returns None (encrypted_mcp_config
          is populated separately via model serializer)
        - If expose_secrets flag is True in context: returns the config as-is
        - Otherwise: returns None to redact sensitive MCP configuration
        """
        if not v:
            # Empty config - nothing to protect
            return v

        # If cipher is present, we'll encrypt instead (handled in model dump)
        if info.context and info.context.get("cipher"):
            return None

        # If expose_secrets is True, return the config as-is
        if info.context and info.context.get("expose_secrets"):
            return v

        # Default: redact by returning None
        return None

    @model_serializer(mode="wrap")
    def _serialize_with_encrypted_mcp(self, handler, info: SerializationInfo):
        """Serialize the agent, handling mcp_config encryption.

        This serializer also handles polymorphic serialization for subclasses
        (like ACPAgent) by delegating to model_dump when the handler is not
        for the actual class.

        When a cipher is present in context and mcp_config has content:
        - Encrypts mcp_config as JSON and stores in encrypted_mcp_config
        - Removes the None mcp_config from output
        """
        if isinstance(self, dict):
            # Sometimes pydantic passes a dict in here.
            return self

        # Check if handler is for the current (actual) class
        handler_str = str(handler)
        _, handler_class = handler_str.split("=", 1)
        handler_class = handler_class[:-1]  # Remove trailing )

        if handler_class != self.__class__.__name__:
            # Handler is for a base class, delegate to model_dump for proper
            # subclass serialization (e.g., ACPAgent fields)
            result = self.model_dump(
                mode=info.mode,
                context=info.context,
                by_alias=info.by_alias,
                exclude_unset=info.exclude_unset,
                exclude_defaults=info.exclude_defaults,
                exclude_none=info.exclude_none,
                round_trip=info.round_trip,
                serialize_as_any=info.serialize_as_any,
            )
        else:
            result = handler(self)

        # Add encrypted_mcp_config if cipher is present and mcp_config has content
        if self.mcp_config and info.context and info.context.get("cipher"):
            cipher: Cipher = info.context["cipher"]
            json_str = json.dumps(self.mcp_config)
            encrypted = cipher.encrypt(SecretStr(json_str))
            if encrypted:
                result["encrypted_mcp_config"] = encrypted

        # Redact by omitting: remove mcp_config if it was set to None by the
        # field serializer (either due to cipher encryption or default redaction).
        # This preserves the REST API contract (field is optional, not nullable).
        if "mcp_config" in result and result["mcp_config"] is None:
            del result["mcp_config"]

        return result

    condenser: CondenserBase | None = Field(
        default=None,
        description="Optional condenser to use for condensing conversation history.",
        examples=[
            {
                "kind": "LLMSummarizingCondenser",
                "llm": {
                    "model": "litellm_proxy/anthropic/claude-sonnet-4-5-20250929",
                    "base_url": "https://llm-proxy.eval.all-hands.dev",
                    "api_key": "your_api_key_here",
                },
                "max_size": 80,
                "keep_first": 10,
            }
        ],
    )

    critic: CriticBase | None = Field(
        default=None,
        description=(
            "EXPERIMENTAL: Optional critic to evaluate agent actions and messages "
            "in real-time. API and behavior may change without notice. "
            "May impact performance, especially in 'all_actions' mode."
        ),
        examples=[{"kind": "AgentFinishedCritic"}],
    )

    tool_concurrency_limit: int = Field(
        default=1,
        ge=1,
        description=(
            "Maximum number of tool calls to execute concurrently within a single "
            "agent step. Default is 1 (sequential). Values > 1 enable parallel "
            "execution; concurrent tools share the conversation object, filesystem, "
            "and working directory, so mutations to shared state may race."
        ),
    )

    # Runtime materialized tools; private and non-serializable
    _tools: dict[str, ToolDefinition] = PrivateAttr(default_factory=dict)
    _initialized: bool = PrivateAttr(default=False)

    @property
    def prompt_dir(self) -> str:
        """Returns the directory where this class's module file is located."""
        module = sys.modules[self.__class__.__module__]
        module_file = module.__file__  # e.g. ".../mypackage/mymodule.py"
        if module_file is None:
            raise ValueError(f"Module file for {module} is None")
        return os.path.join(os.path.dirname(module_file), "prompts")

    @property
    def name(self) -> str:
        """Returns the name of the Agent."""
        return self.__class__.__name__

    @property
    def static_system_message(self) -> str:
        """Compute the static portion of the system message.

        This returns only the base system prompt template without any dynamic
        per-conversation context. This static portion can be cached and reused
        across conversations for better prompt caching efficiency.

        When ``system_prompt`` is set, that string is returned verbatim,
        bypassing Jinja2 template rendering entirely.

        Returns:
            The rendered system prompt template without dynamic context.
        """
        if self.system_prompt is not None:
            return self.system_prompt

        template_kwargs = dict(self.system_prompt_kwargs)
        # Auto-detect browser tools from the tool spec list
        template_kwargs.setdefault(
            "enable_browser",
            any(t.name == "browser_tool_set" for t in self.tools),
        )
        # Add security_policy_filename to template kwargs
        template_kwargs["security_policy_filename"] = self.security_policy_filename
        template_kwargs.setdefault("model_name", self.llm.model)
        if (
            "model_family" not in template_kwargs
            or "model_variant" not in template_kwargs
        ):
            spec = get_model_prompt_spec(
                self.llm.model, getattr(self.llm, "model_canonical_name", None)
            )
            if "model_family" not in template_kwargs and spec.family:
                template_kwargs["model_family"] = spec.family
            if "model_variant" not in template_kwargs and spec.variant:
                template_kwargs["model_variant"] = spec.variant
        return render_template(
            prompt_dir=self.prompt_dir,
            template_name=self.system_prompt_filename,
            **template_kwargs,
        )

    @property
    def dynamic_context(self) -> str | None:
        """Get the dynamic per-conversation context.

        This returns the context that varies between conversations, such as:
        - Repository information and skills
        - Runtime information (hosts, working directory)
        - User-specific secrets and settings
        - Conversation instructions

        This content should NOT be included in the cached system prompt to enable
        cross-conversation cache sharing. Instead, it is sent as a second content
        block (without a cache marker) inside the system message.

        Returns:
            The dynamic context string, or None if no context is configured.
        """
        if not self.agent_context:
            return None
        return self.agent_context.get_system_message_suffix(
            llm_model=self.llm.model,
            llm_model_canonical=self.llm.model_canonical_name,
        )

    def init_state(
        self,
        state: ConversationState,
        on_event: ConversationCallbackType,  # noqa: ARG002
    ) -> None:
        """Initialize the empty conversation state to prepare the agent for user
        messages.

        Typically this involves adding system message

        NOTE: state will be mutated in-place.
        """
        self._initialize(state)

    def _initialize(self, state: ConversationState):
        """Create an AgentBase instance from an AgentSpec."""

        if self._initialized:
            logger.warning("Agent already initialized; skipping re-initialization.")
            return

        tools: list[ToolDefinition] = []

        # Use ThreadPoolExecutor to parallelize tool resolution
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = []

            # Submit tool resolution tasks
            for tool_spec in self.tools:
                future = executor.submit(resolve_tool, tool_spec, state)
                futures.append(future)

            # Submit MCP tools creation if configured
            if self.mcp_config:
                future = executor.submit(create_mcp_tools, self.mcp_config, 30)
                futures.append(future)

            # Collect results as they complete
            for future in futures:
                result = future.result()
                tools.extend(result)

        logger.info(
            f"Loaded {len(tools)} tools from spec: {[tool.name for tool in tools]}"
        )
        if self.filter_tools_regex:
            pattern = re.compile(self.filter_tools_regex)
            tools = [tool for tool in tools if pattern.match(tool.name)]
            logger.info(
                f"Filtered to {len(tools)} tools after applying regex filter: "
                f"{[tool.name for tool in tools]}",
            )

        # Include default tools from include_default_tools; not subject to regex
        # filtering. Use explicit mapping to resolve tool class names.
        # Auto-attach `InvokeSkillTool` iff an AgentSkills-format skill is
        # loaded and the user hasn't already opted in explicitly.
        has_agentskills = bool(
            self.agent_context
            and any(s.is_agentskills_format for s in self.agent_context.skills)
        )
        default_tool_names = list(self.include_default_tools)
        if has_agentskills and InvokeSkillTool.__name__ not in default_tool_names:
            default_tool_names.append(InvokeSkillTool.__name__)
            logger.debug(
                "Auto-attached %s (AgentSkills-format skill present in agent_context)",
                InvokeSkillTool.__name__,
            )

        for tool_name in default_tool_names:
            tool_class = BUILT_IN_TOOL_CLASSES.get(tool_name)
            if tool_class is None:
                raise ValueError(
                    f"Unknown built-in tool class: '{tool_name}'. "
                    f"Expected one of: {list(BUILT_IN_TOOL_CLASSES.keys())}"
                )
            tool_instances = tool_class.create(state)
            tools.extend(tool_instances)

        # Check tool types
        for tool in tools:
            if not isinstance(tool, ToolDefinition):
                raise ValueError(
                    f"Tool {tool} is not an instance of 'ToolDefinition'. "
                    f"Got type: {type(tool)}"
                )

        # Check name duplicates
        tool_names = [tool.name for tool in tools]
        if len(tool_names) != len(set(tool_names)):
            duplicates = set(name for name in tool_names if tool_names.count(name) > 1)
            raise ValueError(f"Duplicate tool names found: {duplicates}")

        # Store tools in a dict for easy access
        self._tools = {tool.name: tool for tool in tools}
        self._initialized = True

    @abstractmethod
    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        """Taking a step in the conversation.

        Typically this involves:
        1. Making a LLM call
        2. Executing the tool
        3. Updating the conversation state with
            LLM calls (role="assistant") and tool results (role="tool")
        4.1 If conversation is finished, set state.execution_status to FINISHED
        4.2 Otherwise, just return, Conversation will kick off the next step

        If the underlying LLM supports streaming, partial deltas are forwarded to
        ``on_token`` before the full response is returned.

        NOTE: state will be mutated in-place.
        """

    def verify(
        self,
        persisted: AgentBase,
        events: Sequence[Any] | None = None,  # noqa: ARG002
    ) -> AgentBase:
        """Verify that we can resume this agent from persisted state.

        We do not merge configuration between persisted and runtime Agent
        instances. Instead, we verify compatibility requirements and then
        continue with the runtime-provided Agent.

        Compatibility requirements:
        - Agent class/type must match.
        - Tools may only be added, never removed.

        Removing tools breaks backward compatibility because the LLM may have
        already been told about them.  Adding new tools is safe — the LLM
        simply gains new capabilities on the next turn.

        All other configuration (LLM, agent_context, condenser, etc.) can be
        freely changed between sessions.

        Args:
            persisted: The agent loaded from persisted state.
            events: Unused, kept for API compatibility.

        Returns:
            This runtime agent (self) if verification passes.

        Raises:
            ValueError: If agent class or tools don't match.
        """
        if persisted.__class__ is not self.__class__:
            raise ValueError(
                "Cannot load from persisted: persisted agent is of type "
                f"{persisted.__class__.__name__}, but self is of type "
                f"{self.__class__.__name__}."
            )

        # Collect explicit tool names
        runtime_names = {tool.name for tool in self.tools}
        persisted_names = {tool.name for tool in persisted.tools}

        # Add builtin tool names from include_default_tools
        # These are runtime names like 'finish', 'think'
        for tool_class_name in self.include_default_tools:
            tool_class = BUILT_IN_TOOL_CLASSES.get(tool_class_name)
            if tool_class is not None:
                runtime_names.add(tool_class.name)

        for tool_class_name in persisted.include_default_tools:
            tool_class = BUILT_IN_TOOL_CLASSES.get(tool_class_name)
            if tool_class is not None:
                persisted_names.add(tool_class.name)

        # Removing tools breaks backward compatibility because the LLM may
        # have already been told about them.  Adding new tools is safe — the
        # LLM simply gains new capabilities on the next turn.
        missing_in_runtime = persisted_names - runtime_names
        if missing_in_runtime:
            raise ValueError(
                f"Cannot resume conversation: tools were removed mid-conversation "
                f"(removed: {sorted(missing_in_runtime)}). "
                f"To use different tools, start a new conversation."
            )

        return self

    def model_dump_succint(self, **kwargs):
        """Like model_dump, but excludes None fields by default."""
        if "exclude_none" not in kwargs:
            kwargs["exclude_none"] = True
        dumped = super().model_dump(**kwargs)
        # remove tool schema details for brevity
        if "tools" in dumped and isinstance(dumped["tools"], dict):
            dumped["tools"] = list(dumped["tools"].keys())
        return dumped

    def get_all_llms(self) -> Generator[LLM]:
        """Recursively yield unique *base-class* LLM objects reachable from `self`.

        - Returns actual object references (not copies).
        - De-dupes by `id(LLM)`.
        - Cycle-safe via a visited set for *all* traversed objects.
        - Only yields objects whose type is exactly `LLM` (no subclasses).
        - Does not handle dataclasses.
        """
        yielded_ids: set[int] = set()
        visited: set[int] = set()

        def _walk(obj: object) -> Iterable[LLM]:
            oid = id(obj)
            # Guard against cycles on anything we might recurse into
            if oid in visited:
                return ()
            visited.add(oid)

            # Traverse LLM based classes and its fields
            # e.g., LLMRouter that is a subclass of LLM
            # yet contains LLM in its fields
            if isinstance(obj, LLM):
                llm_out: list[LLM] = []

                # Yield only the *raw* base-class LLM (exclude subclasses)
                if type(obj) is LLM and oid not in yielded_ids:
                    yielded_ids.add(oid)
                    llm_out.append(obj)

                # Traverse all fields for LLM objects
                for name in type(obj).model_fields:
                    try:
                        val = getattr(obj, name)
                    except Exception:
                        continue
                    llm_out.extend(_walk(val))
                return llm_out

            # Pydantic models: iterate declared fields
            if isinstance(obj, BaseModel):
                model_out: list[LLM] = []
                for name in type(obj).model_fields:
                    try:
                        val = getattr(obj, name)
                    except Exception:
                        continue
                    model_out.extend(_walk(val))
                return model_out

            # Built-in containers
            if isinstance(obj, dict):
                dict_out: list[LLM] = []
                for k, v in obj.items():
                    dict_out.extend(_walk(k))
                    dict_out.extend(_walk(v))
                return dict_out

            if isinstance(obj, (list, tuple, set, frozenset)):
                container_out: list[LLM] = []
                for item in obj:
                    container_out.extend(_walk(item))
                return container_out

            # Unknown object types: nothing to do
            return ()

        # Drive the traversal from self
        yield from _walk(self)

    @property
    def tools_map(self) -> dict[str, ToolDefinition]:
        """Get the initialized tools map.
        Raises:
            RuntimeError: If the agent has not been initialized.
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized; call _initialize() before use")
        return self._tools

    def ask_agent(self, question: str) -> str | None:  # noqa: ARG002
        """Optional override for stateless question answering.

        Subclasses (e.g. ACPAgent) may override this to provide their own
        implementation of ask_agent that bypasses the default LLM-based path.

        Returns:
            Response string, or ``None`` to use the default LLM-based approach.
        """
        return None

    def close(self) -> None:
        """Clean up agent resources.

        No-op by default; ACPAgent overrides to terminate subprocess.
        """
        pass
