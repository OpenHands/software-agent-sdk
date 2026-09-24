import json
import os
import time
import traceback
import uuid
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from litellm.cost_calculator import completion_cost as litellm_completion_cost
from litellm.types.llms.openai import ResponseAPIUsage, ResponsesAPIResponse
from litellm.types.utils import CostPerToken, ModelResponse, Usage
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from openhands.sdk.llm.utils.litellm_provider import LLMProvider
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.llm.utils.openhands_provider import litellm_call_kwargs
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """Provider-independent view of one response's token accounting.

    LiteLLM reports usage in two disjoint shapes that name the same quantities
    differently: ``Usage`` (Chat Completions) uses ``prompt_tokens`` /
    ``completion_tokens``, while ``ResponseAPIUsage`` (Responses API) uses
    ``input_tokens`` / ``output_tokens``. Normalizing once, at the boundary,
    keeps every consumer -- metrics, span attributes and completion logs -- on
    a single vocabulary instead of rediscovering the aliases at each call site.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def has_tokens(self) -> bool:
        """Whether the provider reported any prompt or completion tokens."""
        return self.prompt_tokens > 0 or self.completion_tokens > 0


def normalize_usage(usage: Usage | ResponseAPIUsage | None) -> UsageSnapshot | None:
    """Return a :class:`UsageSnapshot` for either provider usage shape.

    ``None`` means "no token accounting": the provider reported no usage, or
    reported a shape this module does not recognize. Unrecognized shapes are
    ignored rather than guessed at, so adding a provider cannot silently feed
    invented token counts into cost and cache accounting.
    """
    if isinstance(usage, Usage):
        prompt_details = usage.prompt_tokens_details
        completion_details = usage.completion_tokens_details
        # ``cache_creation_tokens`` defaults to ``None``, so presence in
        # ``model_fields_set`` is what distinguishes "provider reported a cache
        # write" from "provider said nothing about cache writes".
        cache_write = 0
        if (
            prompt_details is not None
            and "cache_creation_tokens" in prompt_details.model_fields_set
        ):
            cache_write = int(prompt_details.cache_creation_tokens or 0)
        return UsageSnapshot(
            prompt_tokens=int(usage.prompt_tokens or 0),
            completion_tokens=int(usage.completion_tokens or 0),
            reasoning_tokens=(
                int(completion_details.reasoning_tokens or 0)
                if completion_details is not None
                else 0
            ),
            cache_read_tokens=(
                int(prompt_details.cached_tokens or 0)
                if prompt_details is not None
                else 0
            ),
            cache_write_tokens=cache_write,
        )

    if isinstance(usage, ResponseAPIUsage):
        input_details = usage.input_tokens_details
        output_details = usage.output_tokens_details
        return UsageSnapshot(
            prompt_tokens=int(usage.input_tokens or 0),
            completion_tokens=int(usage.output_tokens or 0),
            reasoning_tokens=(
                int(output_details.reasoning_tokens or 0)
                if output_details is not None
                else 0
            ),
            cache_read_tokens=(
                int(input_details.cached_tokens or 0)
                if input_details is not None
                else 0
            ),
        )

    return None


def _response_usage(
    resp: ModelResponse | ResponsesAPIResponse,
) -> Usage | ResponseAPIUsage | None:
    """Return the usage object a response carries, if any.

    ``ResponsesAPIResponse`` declares ``usage`` as a field. ``ModelResponse``
    only accepts it as a constructor argument, so pydantic keeps it in the
    model's extra mapping instead of its fields; reading that mapping is the
    typed equivalent of probing for a possibly-absent attribute.
    """
    if isinstance(resp, ResponsesAPIResponse):
        return resp.usage
    if isinstance(resp, ModelResponse):
        extra = resp.model_extra
        if not extra:
            return None
        usage = extra.get("usage")
        if isinstance(usage, (Usage, ResponseAPIUsage)):
            return usage
    return None


class Telemetry(BaseModel):
    """
    Handles latency, token/cost accounting, and optional logging.
    All runtime state (like start times) lives in private attrs.
    """

    # --- Config fields ---
    model_name: str = Field(default="unknown", description="Name of the LLM model")
    log_enabled: bool = Field(default=False, description="Whether to log completions")
    log_dir: str | None = Field(
        default=None, description="Directory to write logs if enabled"
    )
    input_cost_per_token: float | None = Field(
        default=None, ge=0, description="Custom Input cost per token (USD)"
    )
    output_cost_per_token: float | None = Field(
        default=None, ge=0, description="Custom Output cost per token (USD)"
    )

    metrics: Metrics = Field(..., description="Metrics collector instance")

    # --- Runtime fields (not serialized) ---
    _req_start: float = PrivateAttr(default=0.0)
    _req_ctx: dict[str, Any] = PrivateAttr(default_factory=dict)
    _last_latency: float = PrivateAttr(default=0.0)
    _log_completions_callback: Callable[[str, str], None] | None = PrivateAttr(
        default=None
    )
    _stats_update_callback: Callable[[], None] | None = PrivateAttr(default=None)
    _span_cm: Any = PrivateAttr(default=None)
    _span: Any = PrivateAttr(default=None)

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", arbitrary_types_allowed=True
    )

    # ---------- Lifecycle ----------
    def set_log_completions_callback(
        self, callback: Callable[[str, str], None] | None
    ) -> None:
        """Set a callback function for logging instead of writing to file.

        Args:
            callback: A function that takes (filename, log_data) and handles the log.
                     Used for streaming logs in remote execution contexts.
        """
        self._log_completions_callback = callback

    def set_stats_update_callback(self, callback: Callable[[], None] | None) -> None:
        """Set a callback function to be notified when stats are updated.

        Args:
            callback: A function called whenever metrics are updated.
                     Used for streaming stats updates in remote execution contexts.
        """
        self._stats_update_callback = callback

    def on_request(self, telemetry_ctx: dict | None) -> None:
        self._req_start = time.time()
        self._req_ctx = telemetry_ctx or {}
        self._open_span()

    def on_response(
        self,
        resp: ModelResponse | ResponsesAPIResponse,
        raw_resp: ModelResponse | None = None,
        provider_info: LLMProvider | None = None,
    ) -> Metrics:
        """
        Side-effects:
          - records latency, tokens, cost into Metrics
          - optionally writes a JSON log file
        """
        # 1) latency
        self._last_latency = time.time() - (self._req_start or time.time())
        response_id = resp.id
        self.metrics.add_response_latency(self._last_latency, response_id)

        # 2) cost
        cost = self._compute_cost(resp, provider_info=provider_info)
        # Intentionally skip logging zero-cost (0.0) responses; only record
        # positive cost
        if cost:
            self.metrics.add_cost(cost)

        # 3) tokens - normalize the provider's usage shape exactly once
        usage = normalize_usage(_response_usage(resp))

        if usage is not None and usage.has_tokens:
            self._record_usage(
                usage, response_id, self._req_ctx.get("context_window", 0)
            )

        # 4) optional logging
        if self.log_enabled:
            self.log_llm_call(resp, cost, raw_resp=raw_resp)

        # 5) authoritative cost + cache buckets onto the span, before it closes
        self._annotate_span(cost, usage)
        self._close_span()

        # 6) notify about stats update
        if self._stats_update_callback is not None:
            try:
                self._stats_update_callback()
            except Exception:
                logger.exception("Stats update callback failed", exc_info=True)

        return self.metrics.deep_copy()

    def on_error(self, _err: BaseException) -> None:
        # Best-effort logging for failed requests (so we can debug malformed
        # request payloads, e.g. orphaned Responses reasoning items).
        self._last_latency = time.time() - (self._req_start or time.time())
        self._close_span(_err)

        if not self.log_enabled:
            return
        if not self.log_dir and not self._log_completions_callback:
            return

        try:
            filename = (
                f"{self.model_name.replace('/', '__')}-"
                f"{time.time():.3f}-"
                f"{uuid.uuid4().hex[:4]}-error.json"
            )

            data = self._req_ctx.copy()
            data["error"] = {
                "type": type(_err).__name__,
                "message": str(_err),
                "repr": repr(_err),
                "traceback": "".join(
                    traceback.format_exception(type(_err), _err, _err.__traceback__)
                ),
            }
            data["timestamp"] = time.time()
            data["latency_sec"] = self._last_latency
            data["cost"] = 0.0

            log_data = json.dumps(data, default=_safe_json, ensure_ascii=False)

            if self._log_completions_callback:
                self._log_completions_callback(filename, log_data)
            elif self.log_dir:
                os.makedirs(self.log_dir, exist_ok=True)
                fname = os.path.join(self.log_dir, filename)
                with open(fname, "w", encoding="utf-8") as f:
                    f.write(log_data)
        except Exception as e:
            warnings.warn(f"Telemetry error logging failed: {e}")
        return

    # ---------- Helpers ----------
    def _record_usage(
        self, usage: UsageSnapshot, response_id: str, context_window: int
    ) -> None:
        """Record an already-normalized usage snapshot into ``metrics``."""
        self.metrics.add_token_usage(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            context_window=context_window,
            response_id=response_id,
        )

    # ---------- Observability span ----------
    # These bracket one LLM call: ``on_request`` -> transport -> ``on_response``
    # all run inside a single retry attempt, so the span is current while
    # litellm executes and still open when the cost is known.
    def _open_span(self) -> None:
        self._close_span()  # a retry re-enters on_request; never leak the old one
        try:
            # Imported lazily: openhands.sdk.observability pulls in
            # openhands.sdk.event, which imports this module.
            from openhands.sdk.observability.laminar import llm_call_span

            cm = llm_call_span(f"llm.{self.model_name}")
            self._span = cm.__enter__()
            self._span_cm = cm
        except Exception:
            logger.debug("Failed to open LLM span", exc_info=True)
            self._span = self._span_cm = None

    def _annotate_span(self, cost: float | None, usage: UsageSnapshot | None) -> None:
        span = self._span
        if span is None:
            return
        try:
            if cost:
                # Authoritative: _compute_cost prefers the proxy's
                # x-litellm-response-cost header, which is already cache-aware
                # and priced by the real backend rather than by a name lookup.
                span.set_attribute("gen_ai.usage.cost", float(cost))
            if usage is not None:
                # Emitted unconditionally: lmnr only reports these when
                # prompt_tokens_details is populated, which not every provider
                # shape does.
                span.set_attribute(
                    "gen_ai.usage.cache_read_input_tokens", usage.cache_read_tokens
                )
                span.set_attribute(
                    "gen_ai.usage.cache_creation_input_tokens",
                    usage.cache_write_tokens,
                )
        except Exception:
            logger.debug("Failed to annotate LLM span", exc_info=True)

    def _close_span(self, err: BaseException | None = None) -> None:
        cm, self._span_cm, self._span = self._span_cm, None, None
        if cm is None:
            return
        try:
            if err is not None:
                cm.__exit__(type(err), err, err.__traceback__)
            else:
                cm.__exit__(None, None, None)
        except Exception:
            logger.debug("Failed to close LLM span", exc_info=True)

    def _compute_cost(
        self,
        resp: ModelResponse | ResponsesAPIResponse,
        provider_info: LLMProvider | None = None,
    ) -> float | None:
        """Try provider header → litellm direct. Return None on failure."""
        extra_kwargs = {}
        if (
            self.input_cost_per_token is not None
            and self.output_cost_per_token is not None
        ):
            cost_per_token = CostPerToken(
                input_cost_per_token=self.input_cost_per_token,
                output_cost_per_token=self.output_cost_per_token,
            )
            logger.debug(f"Using custom cost per token: {cost_per_token}")
            extra_kwargs["custom_cost_per_token"] = cost_per_token

        try:
            hidden = resp._hidden_params or {}
            cost = hidden.get("additional_headers", {}).get(
                "llm_provider-x-litellm-response-cost"
            )
            if cost is not None:
                return float(cost)
        except Exception as e:
            logger.debug(f"Failed to get cost from LiteLLM headers: {e}")

        if provider_info is None:
            call_kwargs = litellm_call_kwargs(self.model_name, None)
            provider_info = LLMProvider.from_model(
                model=call_kwargs["model"],
                api_base=call_kwargs["api_base"],
            )
        extra_kwargs.update(provider_info.as_litellm_call_kwargs())
        try:
            return float(
                litellm_completion_cost(completion_response=resp, **extra_kwargs)
            )
        except Exception as e:
            warnings.warn(f"Cost calculation failed: {e}")
            return None

    def log_llm_call(
        self,
        resp: ModelResponse | ResponsesAPIResponse,
        cost: float | None,
        raw_resp: ModelResponse | ResponsesAPIResponse | None = None,
    ) -> None:
        # Skip if neither file logging nor callback is configured
        if not self.log_dir and not self._log_completions_callback:
            return
        try:
            # Prepare filename and log data
            filename = (
                f"{self.model_name.replace('/', '__')}-"
                f"{time.time():.3f}-"
                f"{uuid.uuid4().hex[:4]}.json"
            )

            data = self._req_ctx.copy()
            data["response"] = (
                resp  # ModelResponse | ResponsesAPIResponse;
                # serialized via _safe_json
            )
            data["cost"] = float(cost or 0.0)
            data["timestamp"] = time.time()
            data["latency_sec"] = self._last_latency

            # Usage summary (prompt, completion, reasoning tokens) for quick inspection
            try:
                usage = normalize_usage(_response_usage(resp))
                if usage is not None:
                    data["usage_summary"] = {
                        "prompt_tokens": usage.prompt_tokens,
                        "completion_tokens": usage.completion_tokens,
                        "reasoning_tokens": usage.reasoning_tokens,
                        "cache_read_tokens": usage.cache_read_tokens,
                    }
            except Exception:
                # Best-effort only; don't fail logging
                pass

            # Raw response *before* nonfncall -> call conversion
            if raw_resp:
                data["raw_response"] = (
                    raw_resp  # ModelResponse | ResponsesAPIResponse;
                    # serialized via _safe_json
                )
            # Pop duplicated tools to avoid logging twice
            if (
                "tools" in data
                and isinstance(data.get("kwargs"), dict)
                and "tools" in data["kwargs"]
            ):
                data["kwargs"].pop("tools")

            log_data = json.dumps(data, default=_safe_json, ensure_ascii=False)

            # Use callback if set (for remote execution), otherwise write to file
            if self._log_completions_callback:
                self._log_completions_callback(filename, log_data)
            elif self.log_dir:
                # Create log directory if it doesn't exist
                os.makedirs(self.log_dir, exist_ok=True)
                if not os.access(self.log_dir, os.W_OK):
                    raise PermissionError(f"log_dir is not writable: {self.log_dir}")
                fname = os.path.join(self.log_dir, filename)
                with open(fname, "w", encoding="utf-8") as f:
                    f.write(log_data)
        except Exception as e:
            warnings.warn(f"Telemetry logging failed: {e}")


def _safe_json(obj: Any) -> Any:
    # Centralized serializer for telemetry logs.
    # Prefer robust serialization for Pydantic models first to avoid cycles.
    # Typed LiteLLM responses
    if isinstance(obj, ModelResponse) or isinstance(obj, ResponsesAPIResponse):
        return obj.model_dump(mode="json", exclude_none=True)

    # Any Pydantic BaseModel (e.g., ToolDefinition, ChatCompletionToolParam, etc.)
    if isinstance(obj, BaseModel):
        # Use Pydantic's serializer which respects field exclusions (e.g., executors)
        return obj.model_dump(mode="json", exclude_none=True)

    # Fallbacks for other non-serializable objects used elsewhere in the log payload
    try:
        return obj.__dict__
    except Exception:
        return str(obj)
