from __future__ import annotations

import os
import re
import sys
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Generator, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    model_validator,
)

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.condenser import CondenserBase
from openhands.sdk.context.prompts.presets import PromptPreset, create_registry
from openhands.sdk.context.prompts.prompt import render_template
from openhands.sdk.context.prompts.section import Platform, PromptContext
from openhands.sdk.critic.base import CriticBase
from openhands.sdk.llm import LLM
from openhands.sdk.llm.utils.model_prompt_spec import get_model_prompt_spec
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.tool import (
    BUILT_IN_TOOL_CLASSES,
    BUILT_IN_TOOLS,
    Tool,
    ToolDefinition,
    resolve_tool,
)
from openhands.sdk.tool.builtins import InvokeSkillTool
from openhands.sdk.tool.builtins.vision_inspect import (
    VisionInspectTool,
    has_vision_profile_available,
)
from openhands.sdk.utils.models import DiscriminatedUnionMixin


if TYPE_CHECKING:
    from openhands.sdk.conversation import ConversationState, LocalConversation
    from openhands.sdk.conversation.types import (
        ConversationCallbackType,
        ConversationTokenCallbackType,
    )

logger = get_logger(__name__)


# -- SOUL.md loader -------------------------------------------------------
# SOUL.md is the agent's identity file (~/.openhands/SOUL.md).  When present
# it replaces the default identity in the system prompt.

_SOUL_PATH = os.path.join(os.path.expanduser("~"), ".openhands", "SOUL.md")
_DEFAULT_SOUL = (
    "You are OpenHands agent, a helpful AI assistant that can interact"
    " with a computer to solve tasks."
)

# Built-in prompt dir. The registry only stands in for built-in prompts here; a
# subclass with its own prompts/ keeps the Jinja render path.
_BUILTIN_PROMPT_DIR = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "prompts")
)

# Built-in ``system_prompt_filename`` values are back-compat sentinels (the .j2 files
# were removed) that select a registry preset. ``system_prompt_planning.j2`` keeps its
# historical name so ``get_planning_agent`` needs no change. Any other filename -- or a
# subclass's own ``prompt_dir`` -- falls through to the Jinja escape hatch.
_PRESET_BY_FILENAME: dict[str, PromptPreset] = {
    "system_prompt.j2": PromptPreset.DEFAULT,
    "system_prompt_planning.j2": PromptPreset.PLANNING,
}


def _load_soul_md() -> str:
    """Load ``~/.openhands/SOUL.md``, falling back to the built-in default."""
    try:
        with open(_SOUL_PATH, encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            return content
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.debug("Could not read SOUL.md from %s: %s", _SOUL_PATH, exc)
    return _DEFAULT_SOUL


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
                "model": "litellm_proxy/openai/gpt-5.5",
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
    mcp_config: dict[str, MCPServer] = Field(
        default_factory=dict,
        description="Optional MCP servers to expose as tools.",
        examples=[{"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}],
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
            "Security policy filename. The default 'security_policy.j2' is a "
            "back-compat sentinel (the file was removed) that selects the built-in "
            "default policy from the prompt registry -- it is not loaded from disk. "
            "Any other value names a custom policy file whose contents are inserted "
            "verbatim (NOT rendered as a Jinja template). Can be either:\n"
            "- A relative filename (e.g., 'custom_security_policy.md') loaded from "
            "the agent's prompts directory\n"
            "- An absolute path (e.g., '/path/to/custom_security_policy.md')\n"
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

    condenser: CondenserBase | None = Field(
        default=None,
        description="Optional condenser to use for condensing conversation history.",
        examples=[
            {
                "kind": "LLMSummarizingCondenser",
                "llm": {
                    "model": "litellm_proxy/openai/gpt-5.5",
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
    def _prompt_preset(self) -> PromptPreset | None:
        """The registry preset for this agent's built-in prompt.

        ``None`` means "take the Jinja escape hatch": a subclass with its own
        ``prompt_dir``, or a ``system_prompt_filename`` that is not a known built-in
        sentinel (e.g. a custom relative name or an absolute path).
        """
        if os.path.realpath(self.prompt_dir) != _BUILTIN_PROMPT_DIR:
            return None
        return _PRESET_BY_FILENAME.get(self.system_prompt_filename)

    @property
    def static_system_message(self) -> str:
        """Compute the static portion of the system message.

        This returns only the base system prompt template without any dynamic
        per-conversation context. This static portion can be cached and reused
        across conversations for better prompt caching efficiency.

        Built-in prompts (the ``default`` and ``planning`` presets) are assembled from
        the typed section registry, which also resolves a custom
        ``security_policy_filename``. Escape hatches keep the Jinja path: an inline
        ``system_prompt`` is returned verbatim; a custom ``system_prompt_filename`` or
        subclass ``prompt_dir`` renders its own template.

        Returns:
            The static system prompt without dynamic context.
        """
        if self.system_prompt is not None:
            return self.system_prompt

        # Escape hatch: a custom filename or a subclass's own prompt_dir renders its
        # own Jinja template; everything else (incl. custom policies) uses the registry.
        preset = self._prompt_preset
        if preset is None:
            return render_template(
                prompt_dir=self.prompt_dir,
                template_name=self.system_prompt_filename,
                **self._resolved_template_kwargs(),
            )

        return create_registry(preset).build(self._build_prompt_context()).static

    def _resolved_template_kwargs(self) -> dict[str, object]:
        """Resolve the system-prompt template kwargs.

        Shared by :pyattr:`static_system_message` and
        :meth:`_build_prompt_context` so the two cannot drift.
        """
        template_kwargs = dict(self.system_prompt_kwargs)

        # Load SOUL.md identity if not already provided
        if "soul_content" not in template_kwargs:
            template_kwargs["soul_content"] = _load_soul_md()

        template_kwargs.setdefault(
            "enable_browser",
            any(t.name == "browser_tool_set" for t in self.tools),
        )
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
        return template_kwargs

    def _read_custom_security_policy(self) -> str | None:
        """Raw contents of a custom security policy file -- inserted verbatim, NOT
        rendered as a Jinja template.

        Returns ``None`` -- so ``SecuritySection`` keeps its built-in default policy
        -- when ``security_policy_filename`` is the default sentinel
        ``"security_policy.j2"`` (a string only; the file was removed, so it is never
        read) or ``""`` (an empty *filename*, which disables the policy). A configured
        file whose own contents are empty still returns ``""`` (an empty custom
        policy), not ``None``.

        Relative names resolve against ``prompt_dir``; absolute paths are used as-is.
        """
        filename = self.security_policy_filename
        if not filename or filename == "security_policy.j2":
            return None
        return (Path(self.prompt_dir) / filename).read_text(encoding="utf-8")

    def _build_prompt_context(
        self,
        additional_secret_infos: list[dict[str, str | None]] | None = None,
    ) -> PromptContext:
        """Frozen :class:`PromptContext` snapshot for this agent.

        ``template_kwargs`` is resolved by the shared
        :meth:`_resolved_template_kwargs`; the other fields snapshot
        per-conversation signals. The dynamic-tier fields reuse
        ``AgentContext._resolve_dynamic_data`` so skills are model-gated and
        secrets merged exactly as ``get_system_message_suffix`` does;
        ``additional_secret_infos`` mirrors ``get_dynamic_context(state)``.
        """
        agent_context = self.agent_context
        # Mirror get_dynamic_context's temp-context path: with no agent_context but
        # conversation secrets present, the legacy renderer resolves a default
        # AgentContext() (which carries a default current_datetime), so its dynamic
        # block advertises the secrets *and* a <CURRENT_DATETIME>. Resolve the same
        # default here so the registry reproduces both blocks, not just secrets.
        if agent_context is None and additional_secret_infos:
            agent_context = AgentContext()

        now: str | None = None
        skill_names: tuple[str, ...] = ()
        secret_names: tuple[str, ...] = ()
        repo_skills: tuple[tuple[str, str], ...] = ()
        available_skills_prompt: str | None = None
        custom_suffix: str | None = None
        secret_infos: tuple[tuple[str, str | None], ...] = ()

        if agent_context is not None:
            data = agent_context._resolve_dynamic_data(
                self.llm.model,
                self.llm.model_canonical_name,
                additional_secret_infos,
            )
            # Reuse the shared resolver's formatted datetime rather than re-deriving
            # it: get_system_message_suffix renders this exact string, so the registry
            # must too (a rounded copy would break byte-for-byte parity for callers
            # that pass a datetime object instead of a pre-formatted string).
            now = data.formatted_datetime
            skill_names = tuple(skill.name for skill in agent_context.skills)
            repo_skills = tuple((s.name, s.content) for s in data.repo_skills)
            available_skills_prompt = data.available_skills_prompt or None
            custom_suffix = agent_context.system_message_suffix or None
            secret_infos = tuple(
                (info["name"] or "", info["description"]) for info in data.secret_infos
            )
            # Derive names from the resolver's merged secret_infos instead of a
            # second get_secret_infos() walk; this now includes registry-provided
            # secrets (additional_secret_infos), matching what <CUSTOM_SECRETS> shows.
            secret_names = tuple(name for name, _ in secret_infos if name)

        template_kwargs = self._resolved_template_kwargs()
        # A custom security policy's content for SecuritySection (registry path only).
        policy_content = self._read_custom_security_policy()
        if policy_content is not None:
            template_kwargs = {
                **template_kwargs,
                "security_policy_content": policy_content,
            }

        return PromptContext(
            template_kwargs=template_kwargs,
            tool_names=tuple(t.name for t in self.tools),
            platform=Platform.current(),
            working_dir=None,
            now=now,
            skill_names=skill_names,
            secret_names=secret_names,
            repo_skills=repo_skills,
            available_skills_prompt=available_skills_prompt,
            custom_suffix=custom_suffix,
            secret_infos=secret_infos,
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

        Assembled from the dynamic-tier sections of the default registry.

        Returns:
            The dynamic context string, or None if no context is configured.
        """
        if not self.agent_context:
            return None
        # The dynamic tier is preset-independent, so a custom Jinja template (preset
        # None) still gets the default dynamic block, exactly as before.
        preset = self._prompt_preset or PromptPreset.DEFAULT
        return create_registry(preset).build(self._build_prompt_context()).dynamic

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

    def _initialize(
        self,
        state: ConversationState,
    ):
        """Create an AgentBase instance from an AgentSpec."""

        if self._initialized:
            return

        tools: list[ToolDefinition] = []

        # Use ThreadPoolExecutor to parallelize tool resolution
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = []

            # Submit tool resolution tasks
            for tool_spec in self.tools:
                future = executor.submit(resolve_tool, tool_spec, state)
                futures.append(future)

            # Collect results as they complete
            for future in futures:
                result = future.result()
                tools.extend(result)

        logger.info("Loaded %d tools from spec", len(tools))
        if self.filter_tools_regex:
            pattern = re.compile(self.filter_tools_regex)
            tools = [tool for tool in tools if pattern.match(tool.name)]
            logger.info("Filtered to %d tools after applying regex filter", len(tools))

        # Include default tools from include_default_tools; not subject to regex
        # filtering. Use explicit mapping to resolve tool class names.
        # Auto-attach `InvokeSkillTool` iff an AgentSkills-format skill is
        # directly invocable and the user hasn't already opted in explicitly.
        has_invocable_agentskills = bool(
            self.agent_context
            and any(
                s.is_agentskills_format and not s.disable_model_invocation
                for s in self.agent_context.skills
            )
        )
        default_tool_names = list(self.include_default_tools)
        if (
            has_invocable_agentskills
            and InvokeSkillTool.__name__ not in default_tool_names
        ):
            default_tool_names.append(InvokeSkillTool.__name__)
            logger.debug(
                "Auto-attached %s (invocable AgentSkills-format skill present)",
                InvokeSkillTool.__name__,
            )
        if (
            not self.llm.vision_is_active()
            and VisionInspectTool.__name__ not in default_tool_names
            and has_vision_profile_available()
        ):
            default_tool_names.append(VisionInspectTool.__name__)
            logger.debug(
                "Auto-attached %s (vision profile available for non-vision model)",
                VisionInspectTool.__name__,
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

    async def astep(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        """Async variant of :meth:`step`.

        Default implementation runs the synchronous ``step()`` in a
        thread via :func:`asyncio.loop.run_in_executor` so that
        blocking tool I/O does not starve the event loop.
        Subclasses that perform async LLM calls should override this.
        """
        import asyncio

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.step, conversation, on_event, on_token)

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

    def _close_tool_executor(self, tool: ToolDefinition) -> None:
        try:
            executable_tool = tool.as_executable()
            executable_tool.executor.close()
        except NotImplementedError:
            return
        except Exception as exc:
            logger.warning("Error closing executor for tool '%s': %s", tool.name, exc)

    def add_runtime_tools(self, tools: Sequence[ToolDefinition]) -> None:
        if not self._initialized:
            logger.warning(
                "add_runtime_tools called before agent initialization; "
                "tools will not be registered"
            )
            return
        for tool in tools:
            if not isinstance(tool, ToolDefinition):
                raise ValueError(
                    f"Tool {tool} is not an instance of 'ToolDefinition'. "
                    f"Got type: {type(tool)}"
                )

        tool_names = [tool.name for tool in tools]
        if len(tool_names) != len(set(tool_names)):
            duplicates = {
                name for name, count in Counter(tool_names).items() if count > 1
            }
            raise ValueError(f"Duplicate runtime tool names found: {duplicates}")
        existing = set(self._tools) & set(tool_names)
        if existing:
            raise ValueError(f"Duplicate tool names found: {existing}")

        # Mutate the tool map in place. ``AgentBase`` is a frozen pydantic
        # model, so the attribute itself cannot be reassigned; the underlying
        # dict object is mutable, though. ``tools_map`` returns a snapshot
        # copy, so readers on other threads (e.g. an MCP client's background
        # loop triggering this method via a tools/list_changed notification)
        # never iterate the live dict while it is being mutated.
        for tool in tools:
            self._tools[tool.name] = tool

    def _on_mcp_tools_changed(self, tools: Sequence[ToolDefinition]) -> None:
        """Handle dynamically advertised MCP tools.

        Invoked on the MCP client's background event-loop thread when an MCP
        server sends ``notifications/tools/list_changed`` (progressive
        disclosure, e.g. Datadog's hosted MCP server). Registers the newly
        added tools so they become callable in subsequent agent steps.
        """
        if not self._initialized:
            logger.warning(
                "MCP tools/list_changed received before agent initialization; "
                "skipping registration of %d tools",
                len(tools),
            )
            return
        try:
            self.add_runtime_tools(tools)
            logger.info(
                "Registered %d dynamically advertised MCP tools: %s",
                len(tools),
                ", ".join(t.name for t in tools),
            )
        except Exception:
            logger.warning(
                "Failed to register dynamically advertised MCP tools",
                exc_info=True,
            )

    @property
    def tools_map(self) -> dict[str, ToolDefinition]:
        """Get the initialized tools map.
        Raises:
            RuntimeError: If the agent has not been initialized.
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized; call _initialize() before use")
        # Return a snapshot copy so callers iterating the result are isolated
        # from concurrent in-place mutations by ``add_runtime_tools`` (which
        # may run on an MCP client's background thread when a server sends
        # ``notifications/tools/list_changed``).
        return dict(self._tools)

    # -- Capability helpers -----------------------------------------------
    # Downstream code should branch on these properties rather than doing
    # ``isinstance(agent, ACPAgent)`` checks.  That keeps the regular/ACP
    # code paths decoupled from the concrete class hierarchy.

    @property
    def supports_openhands_tools(self) -> bool:
        """``True`` if OpenHands can inject tools into this agent.

        ``False`` for :class:`~openhands.sdk.agent.acp_agent.ACPAgent` — the
        ACP server manages its own toolset.
        """
        return True

    @property
    def supports_openhands_mcp(self) -> bool:
        """``True`` if OpenHands can create in-process MCP tools for this agent.

        ``False`` for :class:`~openhands.sdk.agent.acp_agent.ACPAgent` — ACP
        agents pass configured MCP servers through to the ACP subprocess.
        """
        return True

    @property
    def supports_condenser(self) -> bool:
        """``True`` if OpenHands context condensing is supported for this agent.

        ``False`` for :class:`~openhands.sdk.agent.acp_agent.ACPAgent` — the
        ACP server manages its own context window.
        """
        return True

    @property
    def agent_kind(self) -> Literal["openhands", "acp"]:
        """Agent kind, matching the ``agent_kind`` settings discriminator."""
        return "openhands"

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
