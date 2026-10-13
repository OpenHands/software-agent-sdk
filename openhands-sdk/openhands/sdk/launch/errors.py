from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class AgentLaunchError(ValueError):
    """A launch request that cannot be satisfied as given."""

    code = "invalid_agent_launch"

    def to_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self)}


class UnresolvedProfileReferences(AgentLaunchError):
    """Every reference in an Agent Profile that names nothing, reported together."""

    code = "unresolved_profile_references"

    def __init__(
        self,
        *,
        llm_profile_ref: str | None = None,
        condenser_llm_profile_ref: str | None = None,
        mcp_server_refs: Sequence[str] = (),
        meta_profile_ref: str | None = None,
        meta_profile_llm_refs: Sequence[str] = (),
    ) -> None:
        self.llm_profile_ref = llm_profile_ref
        self.condenser_llm_profile_ref = condenser_llm_profile_ref
        self.mcp_server_refs = list(mcp_server_refs)
        self.meta_profile_ref = meta_profile_ref
        self.meta_profile_llm_refs = list(meta_profile_llm_refs)
        problems: list[str] = []
        if llm_profile_ref is not None:
            problems.append(f"LLM profile {llm_profile_ref!r} not found")
        if condenser_llm_profile_ref is not None:
            problems.append(
                f"condenser LLM profile {condenser_llm_profile_ref!r} not found"
            )
        if self.mcp_server_refs:
            problems.append(
                "MCP server(s) not configured: " + ", ".join(self.mcp_server_refs)
            )
        if meta_profile_ref is not None:
            problems.append(f"Meta-profile {meta_profile_ref!r} not found")
        if self.meta_profile_llm_refs:
            problems.append(
                "LLM profile(s) routed to by the meta-profile not found: "
                + ", ".join(self.meta_profile_llm_refs)
            )
        self.problems = problems
        super().__init__("; ".join(problems))

    def to_detail(self) -> dict[str, Any]:
        return {
            **super().to_detail(),
            "dangling_llm_profile_ref": self.llm_profile_ref,
            "dangling_condenser_llm_profile_ref": self.condenser_llm_profile_ref,
            "dangling_mcp_server_refs": self.mcp_server_refs,
            "dangling_meta_profile_ref": self.meta_profile_ref,
            "dangling_meta_profile_llm_refs": self.meta_profile_llm_refs,
        }


class LaunchStoreError(RuntimeError):
    """A store needed by the launch could not be read."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
