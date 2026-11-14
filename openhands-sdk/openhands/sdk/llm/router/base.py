from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import (
    Field,
    SerializeAsAny,
    field_validator,
    model_validator,
)

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.message import Message
from openhands.sdk.llm.router.state import (
    ModelRouteInfo,
    ModelRouterSelection,
    ModelRouterStatus,
)
from openhands.sdk.llm.streaming import AnyTokenCallbackType, TokenCallbackType
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLMCallContext


logger = get_logger(__name__)


class RouterLLM(LLM):
    """
    Base class for multiple LLMs acting as a unified LLM.

    The router owns both the route table and the route-selection state. Automatic
    routing policies implement :meth:`select_llm`; callers can pin a manual route
    by replacing the router with the copy returned by :meth:`with_manual_route`.
    """

    _router_types: ClassVar[dict[str, type[RouterLLM]]] = {}

    router_name: str = Field(default="base_router", description="Name of the router")
    llms_for_routing: dict[str, SerializeAsAny[LLM]] = Field(
        default_factory=dict,
        description="Mapping of route key to LLM instance.",
    )
    selection: ModelRouterSelection = Field(
        default_factory=ModelRouterSelection,
        description="Desired route-selection state for this router.",
    )
    last_selected_llm_key: str | None = Field(
        default=None,
        description="Most recent route chosen by this router.",
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        router_name = cls.model_fields.get("router_name")
        if router_name is None:
            return
        name = router_name.default
        if isinstance(name, str) and name != "base_router":
            RouterLLM._router_types[name] = cls

    @classmethod
    def _known_router_types(cls) -> dict[str, type[RouterLLM]]:
        router_types = dict(cls._router_types)
        pending = list(cls.__subclasses__())
        while pending:
            router_type = pending.pop()
            pending.extend(router_type.__subclasses__())
            field = router_type.model_fields.get("router_name")
            if field is None:
                continue
            name = field.default
            if isinstance(name, str) and name != "base_router":
                router_types[name] = router_type
        return router_types

    @classmethod
    def model_validate_router(cls, data: dict[str, Any]) -> RouterLLM:
        router_name = data.get("router_name")
        router_types = cls._known_router_types()
        if not isinstance(router_name, str):
            known = ", ".join(sorted(router_types)) or "none"
            raise ValueError(
                f"Unknown RouterLLM router_name {router_name!r}; known: {known}"
            )
        router_type = router_types.get(router_name)
        if router_type is None:
            known = ", ".join(sorted(router_types)) or "none"
            raise ValueError(
                f"Unknown RouterLLM router_name {router_name!r}; known: {known}"
            )
        return router_type.model_validate(data)

    @model_validator(mode="before")
    @classmethod
    def _coerce_router_inputs(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)

        if "model" not in d or not d["model"]:
            d["model"] = d.get("router_name", "router")

        if "selection" not in d and "routing_mode" in d:
            mode = d.pop("routing_mode")
            selected_key = d.pop("selected_llm_key", None)
            d["selection"] = {
                "mode": mode,
                "manual_route_key": selected_key if mode == "manual" else None,
            }
            if mode == "auto" and selected_key is not None:
                d.setdefault("last_selected_llm_key", selected_key)
        return d

    @field_validator("llms_for_routing")
    @classmethod
    def validate_llms_not_empty(cls, v: dict[str, LLM]) -> dict[str, LLM]:
        if not v:
            raise ValueError(
                "llms_for_routing cannot be empty - at least one LLM must be provided"
            )
        return v

    @model_validator(mode="after")
    def _validate_selected_route_keys(self) -> Self:
        manual_route_key = self.selection.manual_route_key
        if manual_route_key is not None:
            self._validate_llm_key(manual_route_key)
        if self.last_selected_llm_key is not None:
            self._validate_llm_key(self.last_selected_llm_key)
        return self

    @property
    def active_llm(self) -> LLM | None:
        route_key = self.active_route_key
        if route_key is None:
            return None
        return self.llms_for_routing[route_key]

    @property
    def active_route_key(self) -> str | None:
        if self.selection.mode == "manual":
            return self.selection.manual_route_key
        return self.last_selected_llm_key

    def available_routes(self) -> tuple[str, ...]:
        return tuple(self.llms_for_routing)

    def available_llms(self) -> tuple[str, ...]:
        return self.available_routes()

    def route_infos(self) -> tuple[ModelRouteInfo, ...]:
        return tuple(
            ModelRouteInfo(key=key, model=llm.model, label=key)
            for key, llm in self.llms_for_routing.items()
        )

    def status(self) -> ModelRouterStatus:
        return ModelRouterStatus(
            enabled=True,
            selection=self.selection,
            last_route_key=self.last_selected_llm_key,
            routes=list(self.route_infos()),
        )

    def with_selection(self, selection: ModelRouterSelection) -> Self:
        if selection.manual_route_key is not None:
            self._validate_llm_key(selection.manual_route_key)
        return self.model_copy(update={"selection": selection})

    def with_manual_route(self, route_key: str) -> Self:
        return self.with_selection(
            ModelRouterSelection(mode="manual", manual_route_key=route_key)
        )

    def with_auto_routing(self) -> Self:
        return self.with_selection(ModelRouterSelection(mode="auto"))

    def switch_llm(self, llm_key: str) -> Self:
        return self.with_manual_route(llm_key)

    def resume_auto_routing(self) -> Self:
        return self.with_auto_routing()

    def _validate_llm_key(self, llm_key: str) -> None:
        if llm_key not in self.llms_for_routing:
            available = ", ".join(sorted(self.llms_for_routing)) or "none"
            raise ValueError(
                f"LLM route '{llm_key}' was not found. Available routes: {available}"
            )

    def _select_llm(self, messages: list[Message]) -> tuple[str, LLM]:
        if self.selection.mode == "manual":
            assert self.selection.manual_route_key is not None
            llm_key = self.selection.manual_route_key
        else:
            llm_key = self.select_llm(messages)
        self._validate_llm_key(llm_key)
        self.last_selected_llm_key = llm_key
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
        **kwargs: Any,
    ) -> LLMResponse:
        selected_key, selected_llm = self._select_llm(messages)
        logger.info("RouterLLM routing to %s...", selected_key)
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
        **kwargs: Any,
    ) -> LLMResponse:
        selected_key, selected_llm = self._select_llm(messages)
        logger.info("RouterLLM routing to %s...", selected_key)
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
        **kwargs: Any,
    ) -> LLMResponse:
        selected_key, selected_llm = self._select_llm(messages)
        logger.info("RouterLLM routing to %s...", selected_key)
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
        **kwargs: Any,
    ) -> LLMResponse:
        selected_key, selected_llm = self._select_llm(messages)
        logger.info("RouterLLM routing to %s...", selected_key)
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
        **kwargs: Any,
    ) -> LLMResponse:
        selected_key, selected_llm = self._select_llm(messages)
        logger.info("RouterLLM routing to %s...", selected_key)
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
        **kwargs: Any,
    ) -> LLMResponse:
        selected_key, selected_llm = self._select_llm(messages)
        logger.info("RouterLLM routing to %s...", selected_key)
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
        """Select a route key from ``llms_for_routing`` for this request."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            private_attrs = object.__getattribute__(self, "__pydantic_private__")
            if name in private_attrs:
                return private_attrs[name]
            raise AttributeError(name)
        fallback_llm = self.active_llm or next(iter(self.llms_for_routing.values()))
        if self.active_llm is None:
            logger.info(
                "RouterLLM: No active LLM, using first LLM for attribute '%s'", name
            )
        return getattr(fallback_llm, name)

    def __str__(self) -> str:
        return f"{self.__class__.__name__}(routes={list(self.llms_for_routing)})"
