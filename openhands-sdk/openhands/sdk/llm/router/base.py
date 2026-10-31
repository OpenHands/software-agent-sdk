from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING

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
        return self._select_llm_for_request(messages).completion(
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
        """Async variant of :meth:`completion`, routed identically."""
        return await self._select_llm_for_request(messages).acompletion(
            messages=messages,
            tools=tools,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

    def _select_llm_for_request(self, messages: list[Message]) -> LLM:
        """Resolve the child LLM serving one request.

        The selection is returned to the caller instead of being read back from
        ``active_llm``, so concurrent calls choosing different children cannot
        dispatch to each other's model.
        """
        selected_model = self.select_llm(messages)
        if selected_model not in self.llms_for_routing:
            raise ValueError(
                f"{type(self).__name__}.select_llm() returned "
                f"{selected_model!r}, which is not a key in llms_for_routing "
                f"({sorted(self.llms_for_routing)})"
            )
        selected_llm = self.llms_for_routing[selected_model]

        logger.info(f"RouterLLM routing to {selected_model}...")

        # Kept in sync for backwards compatibility only; dispatch never reads it.
        self.active_llm = selected_llm
        return selected_llm

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

    def __getattr__(self, name):
        """Delegate other attributes/methods to the active LLM."""
        fallback_llm = next(iter(self.llms_for_routing.values()))
        logger.info(f"RouterLLM: No active LLM, using first LLM for attribute '{name}'")
        return getattr(fallback_llm, name)

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
