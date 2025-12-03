from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

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

    def model_dump_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        """Serialize metrics as snapshots to avoid sending lengthy lists.

        This method is intended for use when sending data over WebSocket or
        other network transmission where we want to minimize payload size.
        It converts each Metrics object to a MetricsSnapshot, which excludes
        the costs, response_latencies, and token_usages lists that grow with
        conversation length.

        For full data persistence (e.g., saving state to disk), use the
        standard model_dump() method instead.

        Args:
            **kwargs: Additional arguments to pass to model_dump()

        Returns:
            Dictionary with metrics serialized as snapshots
        """
        # Get the default serialization with full metrics
        data = self.model_dump(**kwargs)

        # Replace each Metrics with its snapshot
        if "usage_to_metrics" in data:
            usage_to_snapshots = {}
            for usage_id, metrics in self.usage_to_metrics.items():
                # Use Metrics.get_snapshot() to convert to MetricsSnapshot
                snapshot = metrics.get_snapshot()
                usage_to_snapshots[usage_id] = snapshot.model_dump()

            data["usage_to_metrics"] = usage_to_snapshots

        return data

    def get_combined_metrics(self) -> Metrics:
        total_metrics = Metrics()
        for metrics in self.usage_to_metrics.values():
            total_metrics.merge(metrics)
        return total_metrics

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
