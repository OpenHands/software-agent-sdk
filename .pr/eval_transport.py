"""Shared request pacing and durable attempt accounting for synchronous evals."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import litellm
from litellm import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk import LLM
from openhands.sdk.llm.streaming import TokenCallbackType


class RequestLimitReached(RuntimeError):
    """The evaluation's total provider-attempt allowance has been exhausted."""


@dataclass
class RequestGate:
    interval: float = 6.0
    max_attempts: int = 250
    initial_attempts: int = 0
    on_attempt: Callable[[int], None] | None = None
    starts: list[float] = field(default_factory=list, init=False)
    _last_start: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.interval) or self.interval < 0:
            raise ValueError("interval must be finite and nonnegative")
        if not 0 <= self.initial_attempts <= self.max_attempts:
            raise ValueError("initial_attempts must be between zero and max_attempts")
        if self.initial_attempts:
            # The last dispatch time is unknown after restarting the evaluator.
            self._last_start = time.monotonic()

    @property
    def attempted_requests(self) -> int:
        return self.initial_attempts + len(self.starts)

    def admit(self) -> None:
        if self.attempted_requests >= self.max_attempts:
            raise RequestLimitReached("Evaluation provider-attempt limit reached")
        if self._last_start is not None:
            delay = max(0.0, self._last_start + self.interval - time.monotonic())
            time.sleep(delay)
        next_attempt = self.attempted_requests + 1
        if self.on_attempt is not None:
            self.on_attempt(next_attempt)
        self._last_start = time.monotonic()
        self.starts.append(self._last_start)
        print(f"Provider attempt {next_attempt}/{self.max_attempts}", flush=True)


class PacedLLM(LLM):
    _gate: RequestGate | None = PrivateAttr(default=None)

    def bind_gate(self, gate: RequestGate) -> None:
        self._gate = gate

    @property
    def attempted_requests(self) -> int:
        return self._gate.attempted_requests if self._gate is not None else 0

    def _transport_call(
        self,
        *,
        messages: list[dict[str, Any]],
        enable_streaming: bool = False,
        on_token: TokenCallbackType | None = None,
        **kwargs,
    ) -> ModelResponse:
        if self._gate is None:
            raise RuntimeError(
                "Bind a shared RequestGate before requesting a completion"
            )
        self._gate.admit()
        litellm.num_retries = 0
        kwargs.update(num_retries=0, max_retries=0)
        return super()._transport_call(
            messages=messages,
            enable_streaming=enable_streaming,
            on_token=on_token,
            **kwargs,
        )
