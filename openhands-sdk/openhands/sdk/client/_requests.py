"""Wire contracts shared by synchronous and asynchronous Agent Server clients."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    options: dict[str, Any] = field(default_factory=dict)
    allowed_statuses: frozenset[int] = frozenset()


def conversation_path(conversation_id: str) -> str:
    return f"/api/conversations/{UUID(conversation_id)}"


def create_conversation(
    *,
    conversation_id: str,
    agent_profile_id: str,
    working_dir: str,
    title: str,
    max_iterations: int = 160,
    tags: dict[str, str] | None = None,
) -> Operation:
    return Operation(
        "POST",
        "/api/conversations",
        {
            "json": {
                "conversation_id": str(UUID(conversation_id)),
                "agent_profile_id": str(UUID(agent_profile_id)),
                "workspace": {"kind": "LocalWorkspace", "working_dir": working_dir},
                "title": title,
                "max_iterations": max_iterations,
                "tags": tags or {},
            },
            "timeout": 180,
        },
    )


def message(conversation_id: str, text: str, run: bool) -> Operation:
    return Operation(
        "POST",
        conversation_path(conversation_id) + "/events",
        {
            "json": {"content": [{"type": "text", "text": text}], "run": run},
        },
    )


def errors(conversation_id: str, limit: int) -> Operation:
    if not 1 <= limit <= 100:
        raise ValueError("Error page limit must be between 1 and 100")
    return Operation(
        "GET",
        conversation_path(conversation_id) + "/events/search",
        {
            "params": {
                "kind": "ConversationErrorEvent",
                "sort_order": "TIMESTAMP_DESC",
                "limit": limit,
            },
        },
    )


class RuntimeRequests:
    """A runtime scope cannot be changed by an operation's arguments."""

    def __init__(self, conversation_id: str | None):
        # None is the explicit legacy host runtime, not an automatic fallback.
        self.api_prefix = (
            conversation_path(conversation_id) if conversation_id else "/api"
        )

    @classmethod
    def from_api_prefix(cls, prefix: str) -> "RuntimeRequests":
        """Compatibility for consumers migrating from a stored API prefix."""
        if prefix == "/api":
            return cls(None)
        root = "/api/conversations/"
        if not prefix.startswith(root):
            raise ValueError("Expected a conversation runtime scope")
        return cls(prefix[len(root) :])

    def upload(self, path: str, content: bytes, filename: str) -> Operation:
        return Operation(
            "POST",
            self.api_prefix + "/file/upload",
            {
                "params": {"path": path},
                "files": {"file": (filename, content)},
            },
        )

    def bash(
        self, command: str, timeout: int, *, background: bool, cwd: str | None = None
    ) -> Operation:
        payload: dict[str, Any] = {"command": command, "timeout": timeout}
        if cwd is not None:
            payload["cwd"] = cwd
        operation = "start_bash_command" if background else "execute_bash_command"
        return Operation(
            "POST",
            self.api_prefix + "/bash/" + operation,
            {
                "json": payload,
                "timeout": 90 if background else timeout + 30,
            },
        )

    def output(self, command_id: str | None) -> Operation:
        params = {"kind__eq": "BashOutput", "sort_order": "TIMESTAMP_DESC", "limit": 1}
        if command_id is not None:
            params["command_id__eq"] = command_id
        return Operation(
            "GET", self.api_prefix + "/bash/bash_events/search", {"params": params}
        )

    def lifecycle(self, *, credentials: bool) -> Operation:
        if self.api_prefix == "/api":
            raise ValueError("Lifecycle operations require a conversation runtime")
        return Operation(
            "POST" if credentials else "DELETE",
            self.api_prefix + ("/runtime/credentials" if credentials else "/runtime"),
            {"timeout": 60},
            frozenset() if credentials else frozenset({404}),
        )
