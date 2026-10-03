from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.message import Message
from openhands.sdk.llm.streaming import AnyTokenCallbackType, TokenCallbackType
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLMCallContext
    from openhands.sdk.llm.utils.runtime_metadata import ModelRuntimeMetadata


logger = get_logger(__name__)


class RouterLLM(LLM):
    """
    Base class for multiple LLM acting as a unified LLM.
    This class provides a foundation for implementing model routing by
    inheriting from LLM, allowing routers to work with multiple underlying
    LLM models while presenting a unified LLM interface to consumers.
    Key features:
    - Works with multiple LLMs configured via llms_for_routing
    - Delegates all other operations/properties to the selected LLM
    - Provides routing interface through select_llm() method
    """

    router_name: str = Field(default="base_router", description="Name of the router")
    llms_for_routing: dict[str, LLM] = Field(
        default_factory=dict
    )  # Mapping of LLM name to LLM instance for routing
    active_llm: LLM | None = Field(
        default=None, description="Currently selected LLM instance"
    )

    @field_validator("llms_for_routing")
    @classmethod
    def validate_llms_not_empty(cls, v):
        if not v:
            raise ValueError(
                "llms_for_routing cannot be empty - at least one LLM must be provided"
            )
        return v

    @property
    def fallback_llm(self) -> LLM:
        """The currently active LLM, or the first configured LLM as fallback."""
        if self.active_llm is not None:
            return self.active_llm
        if not self.llms_for_routing:
            raise AttributeError("RouterLLM has no configured LLMs in llms_for_routing")
        return next(iter(self.llms_for_routing.values()))

    def _select_and_activate(self, messages: list[Message]) -> LLM:
        """Select, activate, and return the routed LLM for given messages."""
        selected_model = self.select_llm(messages)
        if selected_model not in self.llms_for_routing:
            raise KeyError(
                f"Router '{self.router_name}' selected unknown LLM '{selected_model}'. "
                f"Configured models: {list(self.llms_for_routing.keys())}"
            )
        self.active_llm = self.llms_for_routing[selected_model]
        logger.info(f"RouterLLM routing to {selected_model}...")
        return self.active_llm

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        This method intercepts completion calls and routes them to the appropriate
        underlying LLM based on the routing logic implemented in select_llm().

        Args:
            messages: List of conversation messages
            tools: Optional list of tools available to the model
            add_security_risk_prediction: Add security_risk field to tool schemas
            on_token: Optional callback for streaming tokens
            **kwargs: Additional arguments passed to the LLM API

        Note:
            Summary field is always added to tool schemas for transparency and
            explainability of agent actions.
        """
        active_llm = self._select_and_activate(messages)
        return active_llm.completion(
            messages=messages,
            tools=tools,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

    async def acompletion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: AnyTokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Async completion delegated to selected LLM."""
        active_llm = self._select_and_activate(messages)
        return await active_llm.acompletion(
            messages=messages,
            tools=tools,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

    def responses(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        include: list[str] | None = None,
        store: bool | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Responses call delegated to selected LLM."""
        active_llm = self._select_and_activate(messages)
        return active_llm.responses(
            messages=messages,
            tools=tools,
            include=include,
            store=store,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

    async def aresponses(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        include: list[str] | None = None,
        store: bool | None = None,
        add_security_risk_prediction: bool = False,
        on_token: AnyTokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Async responses call delegated to selected LLM."""
        active_llm = self._select_and_activate(messages)
        return await active_llm.aresponses(
            messages=messages,
            tools=tools,
            include=include,
            store=store,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

    def generate(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        include: list[str] | None = None,
        store: bool | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Generate response delegated to selected LLM."""
        active_llm = self._select_and_activate(messages)
        return active_llm.generate(
            messages=messages,
            tools=tools,
            include=include,
            store=store,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

    async def agenerate(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        include: list[str] | None = None,
        store: bool | None = None,
        add_security_risk_prediction: bool = False,
        on_token: AnyTokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Async variant of generate delegated to selected LLM."""
        active_llm = self._select_and_activate(messages)
        return await active_llm.agenerate(
            messages=messages,
            tools=tools,
            include=include,
            store=store,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

    def get_token_count(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
    ) -> int:
        """Delegate token count estimation to fallback LLM."""
        return self.fallback_llm.get_token_count(
            messages=messages,
            tools=tools,
            add_security_risk_prediction=add_security_risk_prediction,
        )

    def vision_is_active(self) -> bool:
        """Delegate vision capability check to fallback LLM."""
        return self.fallback_llm.vision_is_active()

    @property
    def effective_max_input_tokens(self) -> int | None:
        """Delegate effective max input tokens to fallback LLM."""
        return self.fallback_llm.effective_max_input_tokens

    @property
    def effective_max_output_tokens(self) -> int | None:
        """Delegate effective max output tokens to fallback LLM."""
        return self.fallback_llm.effective_max_output_tokens

    def resolve_runtime_metadata(
        self, *, force: bool = False
    ) -> ModelRuntimeMetadata | None:
        """Delegate runtime metadata resolution to configured LLMs."""
        fallback = self.fallback_llm
        fallback_meta: ModelRuntimeMetadata | None = None
        for target_llm in self.llms_for_routing.values():
            meta = target_llm.resolve_runtime_metadata(force=force)
            if target_llm is fallback:
                fallback_meta = meta
        return fallback_meta

    async def aresolve_runtime_metadata(
        self, *, force: bool = False
    ) -> ModelRuntimeMetadata | None:
        """Async delegate runtime metadata resolution to configured LLMs."""
        fallback = self.fallback_llm
        fallback_meta: ModelRuntimeMetadata | None = None
        for target_llm in self.llms_for_routing.values():
            meta = await target_llm.aresolve_runtime_metadata(force=force)
            if target_llm is fallback:
                fallback_meta = meta
        return fallback_meta

    @abstractmethod
    def select_llm(self, messages: list[Message]) -> str:
        """Select which LLM to use based on messages and events.

        This method implements the core routing logic for the RouterLLM.
        Subclasses should analyze the provided messages to determine which
        LLM from llms_for_routing is most appropriate for handling the request.

        Args:
            messages: List of messages in the conversation that can be used
                     to inform the routing decision.

        Returns:
            The key/name of the LLM to use from llms_for_routing dictionary.
        """

    def __getattr__(self, name: str) -> Any:
        """Narrow compatibility boundary: delegate unhandled attributes to fallback LLM.

        This boundary exists for backwards compatibility with dynamic attribute
        access on router instances. Direct capability access should prefer the
        typed LLM interface methods.
        """
        try:
            return super().__getattr__(name)  # pyright: ignore[reportAttributeAccessIssue]
        except AttributeError:
            pass

        if name.startswith("_"):
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{name}'"
            )
        fallback = self.fallback_llm
        logger.info(f"RouterLLM: delegating attribute '{name}' to fallback LLM")
        try:
            return fallback.__getattribute__(name)
        except AttributeError:
            if hasattr(type(fallback), "__getattr__"):
                return type(fallback).__getattr__(fallback, name)  # pyright: ignore[reportAttributeAccessIssue]
            raise

    def __str__(self) -> str:
        """String representation of the router."""
        return f"{self.__class__.__name__}(llms={list(self.llms_for_routing.keys())})"

    @model_validator(mode="before")
    @classmethod
    def set_placeholder_model(cls, data):
        """Guarantee `model` exists before LLM base validation runs."""
        if not isinstance(data, dict):
            return data
        d = dict(data)

        # In router, we don't need a model name to be specified
        if "model" not in d or not d["model"]:
            d["model"] = d.get("router_name", "router")

        return d
