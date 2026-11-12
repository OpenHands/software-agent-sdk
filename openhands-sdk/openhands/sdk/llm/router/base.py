from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, Self

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
        default_factory=dict,
        description="Mapping of route key to LLM instance.",
    )
    routing_mode: Literal["auto", "manual"] = Field(
        default="auto",
        description="Whether the router chooses per call or uses selected_llm_key.",
    )
    selected_llm_key: str | None = Field(
        default=None,
        description="Current route key in manual mode, or the latest auto route.",
    )

    @field_validator("llms_for_routing")
    @classmethod
    def validate_llms_not_empty(cls, v: dict[str, LLM]) -> dict[str, LLM]:
        if not v:
            raise ValueError(
                "llms_for_routing cannot be empty - at least one LLM must be provided"
            )
        return v

    @model_validator(mode="after")
    def _validate_selected_llm_key(self) -> Self:
        if self.routing_mode == "manual" and self.selected_llm_key is None:
            raise ValueError("selected_llm_key is required in manual routing mode")
        if (
            self.selected_llm_key is not None
            and self.selected_llm_key not in self.llms_for_routing
        ):
            raise ValueError(
                f"selected_llm_key '{self.selected_llm_key}' is not present in "
                "llms_for_routing"
            )
        return self

    @property
    def active_llm(self) -> LLM | None:
        if self.selected_llm_key is None:
            return None
        return self.llms_for_routing[self.selected_llm_key]

    def available_llms(self) -> tuple[str, ...]:
        return tuple(self.llms_for_routing)

    def switch_llm(self, llm_key: str) -> Self:
        self._validate_llm_key(llm_key)
        return self.model_copy(
            update={"routing_mode": "manual", "selected_llm_key": llm_key}
        )

    def resume_auto_routing(self) -> Self:
        return self.model_copy(
            update={"routing_mode": "auto", "selected_llm_key": None}
        )

    def _validate_llm_key(self, llm_key: str) -> None:
        if llm_key not in self.llms_for_routing:
            available = ", ".join(sorted(self.llms_for_routing)) or "none"
            raise ValueError(
                f"LLM route '{llm_key}' was not found. Available routes: {available}"
            )

    def _select_llm(self, messages: list[Message]) -> tuple[str, LLM]:
        if self.routing_mode == "manual":
            assert self.selected_llm_key is not None
            llm_key = self.selected_llm_key
        else:
            llm_key = self.select_llm(messages)
        self._validate_llm_key(llm_key)
        self.selected_llm_key = llm_key
        return llm_key, self.llms_for_routing[llm_key]

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
        selected_key, selected_llm = self._select_llm(messages)
        logger.info(f"RouterLLM routing to {selected_key}...")
        return selected_llm.generate(
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
        selected_key, selected_llm = self._select_llm(messages)
        logger.info(f"RouterLLM routing to {selected_key}...")
        return await selected_llm.agenerate(
            messages=messages,
            tools=tools,
            include=include,
            store=store,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs,
    ) -> LLMResponse:
        selected_key, selected_llm = self._select_llm(messages)
        logger.info(f"RouterLLM routing to {selected_key}...")
        return selected_llm.completion(
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
        selected_key, selected_llm = self._select_llm(messages)
        logger.info(f"RouterLLM routing to {selected_key}...")
        return await selected_llm.acompletion(
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
        selected_key, selected_llm = self._select_llm(messages)
        logger.info(f"RouterLLM routing to {selected_key}...")
        return selected_llm.responses(
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
        selected_key, selected_llm = self._select_llm(messages)
        logger.info(f"RouterLLM routing to {selected_key}...")
        return await selected_llm.aresponses(
            messages=messages,
            tools=tools,
            include=include,
            store=store,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

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
        """Delegate unknown public attributes/methods to the selected LLM."""
        if name.startswith("_"):
            private_attrs = object.__getattribute__(self, "__pydantic_private__")
            if name in private_attrs:
                return private_attrs[name]
            raise AttributeError(name)
        fallback_llm = self.active_llm or next(iter(self.llms_for_routing.values()))
        if self.active_llm is None:
            logger.info(
                f"RouterLLM: No active LLM, using first LLM for attribute '{name}'"
            )
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
