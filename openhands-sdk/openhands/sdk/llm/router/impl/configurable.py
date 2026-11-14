from typing import Self

from pydantic import Field, model_validator

from openhands.sdk.llm.message import Message
from openhands.sdk.llm.router.base import RouterLLM


class ConfigurableRouter(RouterLLM):
    """Router that defaults to one route and allows manual route selection."""

    router_name: str = "configurable_router"
    default_route_key: str = Field(
        default="default",
        description="Route key used when selection mode is automatic.",
    )

    @model_validator(mode="after")
    def _validate_default_route_key(self) -> Self:
        self._validate_llm_key(self.default_route_key)
        return self

    def select_llm(self, messages: list[Message]) -> str:  # noqa: ARG002
        return self.default_route_key
