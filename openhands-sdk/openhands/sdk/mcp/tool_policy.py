"""Profile-configured access to tools on an MCP server."""

from pydantic import BaseModel, ConfigDict, Field


class MCPToolPolicy(BaseModel):
    """Tool names are relative to one server, not gateway-qualified names."""

    model_config = ConfigDict(extra="forbid")

    include: list[str] | None = Field(
        default=None, description="None permits all tools; [] permits none."
    )
    exclude: list[str] = Field(
        default_factory=list, description="Tools denied even when included."
    )

    def allows(self, name: str) -> bool:
        return (
            self.include is None or name in self.include
        ) and name not in self.exclude
