from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class ModelRouterSelection(BaseModel):
    mode: Literal["auto", "manual"] = Field(
        default="auto",
        description="Whether routing is automatic or pinned to a manual route.",
    )
    manual_route_key: str | None = Field(
        default=None,
        description="Route key to use when mode is manual.",
    )

    @model_validator(mode="after")
    def _validate_manual_route(self) -> Self:
        if self.mode == "manual" and self.manual_route_key is None:
            raise ValueError("manual_route_key is required in manual routing mode")
        if self.mode == "auto" and self.manual_route_key is not None:
            raise ValueError("manual_route_key is only valid in manual routing mode")
        return self


class ModelRouteInfo(BaseModel):
    key: str = Field(description="Stable route key understood by the model router.")
    model: str | None = Field(
        default=None,
        description="Underlying model configured for this route, when available.",
    )
    label: str | None = Field(
        default=None,
        description="Optional human-readable route label.",
    )


class ModelRouterStatus(BaseModel):
    enabled: bool = Field(description="Whether the conversation LLM is a model router.")
    selection: ModelRouterSelection | None = Field(
        default=None,
        description="Current desired router selection state.",
    )
    last_route_key: str | None = Field(
        default=None,
        description="Most recent route chosen by the router, if any.",
    )
    routes: list[ModelRouteInfo] = Field(
        default_factory=list,
        description="Routes currently available for manual selection.",
    )


class ModelRouterUpdateRequest(BaseModel):
    selection: ModelRouterSelection = Field(
        description="Desired router selection state to apply."
    )
