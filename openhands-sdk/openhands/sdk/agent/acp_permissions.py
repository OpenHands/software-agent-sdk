"""Public permission decisions for external ACP agents."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    PermissionOption,
    RequestPermissionResponse,
    ToolCallUpdate,
)

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

ACPPermissionMode = Literal["legacy-auto-approve", "deny", "callback"]


@dataclass(frozen=True)
class ACPPermissionRequest:
    """Untrusted agent request delivered to a trusted, async host handler.

    A handler must not acquire the conversation state lock: it runs on the
    ACP portal while the caller may hold that lock. Use a separate UI/channel.
    """

    session_id: str
    tool_call: ToolCallUpdate
    options: tuple[PermissionOption, ...]


ACPPermissionHandler = Callable[[ACPPermissionRequest], Awaitable[str | None]]


async def resolve_acp_permission(
    *,
    mode: ACPPermissionMode,
    handler: ACPPermissionHandler | None,
    timeout: float,
    session_id: str,
    tool_call: ToolCallUpdate,
    options: list[PermissionOption],
) -> RequestPermissionResponse:
    """Select an offered option, or cancel without granting permission."""
    if mode == "legacy-auto-approve":
        option_id = options[0].option_id if options else "allow_once"
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=option_id),
        )

    cancelled = RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
    if mode == "deny" or handler is None:
        return cancelled
    offered_ids = tuple(option.option_id for option in options)
    if not offered_ids or len(set(offered_ids)) != len(offered_ids):
        return cancelled

    request = ACPPermissionRequest(
        session_id=session_id,
        tool_call=tool_call.model_copy(deep=True),
        options=tuple(option.model_copy(deep=True) for option in options),
    )
    try:
        selected = await asyncio.wait_for(handler(request), timeout=timeout)
    except Exception:
        # Agent input and handler exceptions may contain secrets.
        logger.warning("ACP permission handler failed or timed out; request cancelled")
        return cancelled
    if selected not in offered_ids:
        return cancelled
    return RequestPermissionResponse(
        outcome=AllowedOutcome(outcome="selected", option_id=selected),
    )
