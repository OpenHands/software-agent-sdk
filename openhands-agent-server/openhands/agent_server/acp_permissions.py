"""Conversation-owned ACP permission requests, independent of the state lock."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel

from openhands.sdk.agent.acp_permissions import ACPPermissionRequest


class ACPPermissionChoice(BaseModel):
    id: UUID
    name: str
    kind: str


class ACPPermissionView(BaseModel):
    request_id: UUID
    session_id: str
    title: str
    kind: str | None
    details: str
    details_truncated: bool
    expires_at: datetime
    options: list[ACPPermissionChoice]


class ACPPermissionPage(BaseModel):
    items: list[ACPPermissionView]


class ACPPermissionDecision(BaseModel):
    option_id: UUID | None


@dataclass
class _Pending:
    view: ACPPermissionView
    choices: dict[UUID, tuple[str, str]]
    deadline: float
    future: Future[str | None]


class ACPPermissionBroker:
    def __init__(self, timeout: float, mask: Callable[[str], str]) -> None:
        self.timeout = timeout
        self.mask = mask
        self._lock = threading.Lock()
        self._pending: dict[UUID, _Pending] = {}
        self._closed = False

    def _masked(self, value):
        if isinstance(value, str):
            return self.mask(value)
        if isinstance(value, dict):
            return {
                self.mask(str(key)): self._masked(item) for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._masked(item) for item in value]
        return value

    async def request(self, request: ACPPermissionRequest) -> str | None:
        details = json.dumps(
            self._masked(request.tool_call.model_dump(mode="json", by_alias=True)),
            ensure_ascii=False,
            indent=2,
        )
        choices = {
            uuid4(): (option.option_id, option.kind) for option in request.options
        }
        view = ACPPermissionView(
            request_id=uuid4(),
            session_id=self.mask(request.session_id),
            title=self.mask(request.tool_call.title or "ACP permission request")[:512],
            kind=request.tool_call.kind,
            details=details[:16384],
            details_truncated=len(details) > 16384,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.timeout),
            options=[
                ACPPermissionChoice(
                    id=key, name=self.mask(option.name)[:256], kind=option.kind
                )
                for key, option in zip(choices, request.options, strict=True)
            ],
        )
        pending = _Pending(view, choices, time.monotonic() + self.timeout, Future())
        with self._lock:
            if self._closed or len(self._pending) >= 16:
                return None
            self._pending[view.request_id] = pending
        try:
            return await asyncio.wait_for(
                asyncio.wrap_future(pending.future), timeout=self.timeout
            )
        except TimeoutError:
            return None
        finally:
            with self._lock:
                self._pending.pop(view.request_id, None)

    def _expire(self) -> None:
        for key, pending in list(self._pending.items()):
            if pending.deadline <= time.monotonic():
                self._pending.pop(key)
                if not pending.future.done():
                    pending.future.set_result(None)

    def snapshot(self) -> ACPPermissionPage:
        with self._lock:
            self._expire()
            return ACPPermissionPage(
                items=[
                    pending.view.model_copy(deep=True)
                    for pending in self._pending.values()
                ]
            )

    def decide(self, request_id: UUID, decision: ACPPermissionDecision) -> bool:
        with self._lock:
            self._expire()
            pending = self._pending.get(request_id)
            if pending is None or pending.future.done():
                return False
            selected = None
            if decision.option_id is not None:
                choice = pending.choices.get(decision.option_id)
                if choice is None:
                    raise ValueError("Option was not offered for this request")
                if pending.view.details_truncated and choice[1].startswith("allow"):
                    raise ValueError("Cannot approve truncated permission details")
                selected = choice[0]
            self._pending.pop(request_id)
            pending.future.set_result(selected)
            return True

    def cancel_pending(self, *, close: bool = False) -> None:
        with self._lock:
            self._closed = self._closed or close
            pending, self._pending = self._pending, {}
            for item in pending.values():
                if not item.future.done():
                    item.future.set_result(None)
