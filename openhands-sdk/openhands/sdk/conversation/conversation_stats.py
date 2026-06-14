from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, model_serializer

from openhands.sdk.llm.llm_registry import RegistryEvent
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class ConversationStats(BaseModel):
    """Track per-LLM usage metrics observed during conversations."""

    usage_to_metrics: dict[str, Metrics] = Field(
        default_factory=dict,
        description="Active usage metrics tracked by the registry.",
    )

    _restored_usage_ids: set[str] = PrivateAttr(default_factory=set)

    @model_serializer(mode="wrap")
    def _serialize_with_context(self, serializer: Any, info: Any) -> dict[str, Any]:
        """Serialize metrics based on context.

        By default, preserves full metrics history including costs,
        response_latencies, and token_usages lists for persistence.

        When context={'use_snapshot': True} is passed, converts Metrics to
        MetricsSnapshot format to minimize payload size for network transmission.

        Args:
            serializer: Pydantic's default serializer
            info: Serialization info containing context

        Returns:
            Dictionary with metrics serialized based on context
        """
        # Get the default serialization
        data = serializer(self)

        # Check if we should use snapshot serialization
        context = info.context if info else None
        use_snapshot = context.get("use_snapshot", False) if context else False

        if use_snapshot and "usage_to_metrics" in data:
            # Replace each Metrics with its snapshot
            usage_to_snapshots = {}
            for usage_id, metrics in self.usage_to_metrics.items():
                snapshot = metrics.get_snapshot()
                usage_to_snapshots[usage_id] = snapshot.model_dump()

            data["usage_to_metrics"] = usage_to_snapshots

        return data

    def get_combined_metrics(self) -> Metrics:
        total_metrics = Metrics()
        for metrics in self.usage_to_metrics.values():
            total_metrics.merge(metrics)
        return total_metrics

    def cache_efficiency_summary(self) -> dict[str, Any]:
        """Return an aggregate snapshot of prompt-cache efficiency.

        Designed as a structured probe that dashboards and eval harnesses
        can call directly — no scraping log lines, no per-LLM iteration on
        the caller side. Sums across every LLM the conversation has used,
        plus a per-LLM breakdown so a single noisy participant doesn't
        hide behind a healthy aggregate.

        Returns:
            A dict with the shape::

                {
                    "prompt_tokens": int,        # total prompt tokens billed
                    "cache_read_tokens": int,    # served from cache
                    "cache_write_tokens": int,   # written to cache
                    "completion_tokens": int,
                    "cache_hit_rate": float | None,    # in [0, 1]
                    "cache_write_rate": float | None,  # in [0, 1]
                    "per_usage": {
                        "<usage_id>": {
                            "model": str,
                            "prompt_tokens": int,
                            "cache_read_tokens": int,
                            "cache_write_tokens": int,
                            "cache_hit_rate": float | None,
                        },
                        ...
                    },
                }

            ``cache_hit_rate`` is ``None`` when no prompt tokens were
            billed (lets callers distinguish "no data" from "0% hit").
        """
        per_usage: dict[str, dict[str, Any]] = {}
        total_prompt = 0
        total_completion = 0
        total_cache_read = 0
        total_cache_write = 0

        def _rate(num: int, den: int) -> float | None:
            return min(1.0, num / den) if den > 0 else None

        for usage_id, metrics in self.usage_to_metrics.items():
            usage = metrics.accumulated_token_usage
            if usage is None:
                continue
            total_prompt += usage.prompt_tokens
            total_completion += usage.completion_tokens
            total_cache_read += usage.cache_read_tokens
            total_cache_write += usage.cache_write_tokens
            per_usage[usage_id] = {
                "model": usage.model or metrics.model_name,
                "prompt_tokens": usage.prompt_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "cache_hit_rate": _rate(usage.cache_read_tokens, usage.prompt_tokens),
            }

        if total_prompt > 0:
            hit_rate: float | None = min(1.0, total_cache_read / total_prompt)
            write_rate: float | None = min(1.0, total_cache_write / total_prompt)
        else:
            hit_rate = None
            write_rate = None

        return {
            "prompt_tokens": total_prompt,
            "cache_read_tokens": total_cache_read,
            "cache_write_tokens": total_cache_write,
            "completion_tokens": total_completion,
            "cache_hit_rate": hit_rate,
            "cache_write_rate": write_rate,
            "per_usage": per_usage,
        }

    def get_metrics_for_usage(self, usage_id: str) -> Metrics:
        if usage_id not in self.usage_to_metrics:
            raise Exception(f"LLM usage does not exist {usage_id}")

        return self.usage_to_metrics[usage_id]

    def register_llm(self, event: RegistryEvent):
        # Listen for LLM creations and track their metrics
        llm = event.llm
        usage_id = llm.usage_id

        # Usage costs exist but have not been restored yet
        if (
            usage_id in self.usage_to_metrics
            and usage_id not in self._restored_usage_ids
        ):
            llm.restore_metrics(self.usage_to_metrics[usage_id])
            self._restored_usage_ids.add(usage_id)

        # Usage is new, track its metrics
        if usage_id not in self.usage_to_metrics and llm.metrics:
            self.usage_to_metrics[usage_id] = llm.metrics
