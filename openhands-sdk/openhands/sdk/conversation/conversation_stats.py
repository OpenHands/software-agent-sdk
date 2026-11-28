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
        # merge() never populates model_name; attribute the aggregate when
        # the rolled-up usage is single-model, and keep "default" otherwise
        # so mixed usage (e.g. a condenser on another model) isn't misattributed.
        # A child's mixed rollup is already "default", so counting it as
        # non-model-bearing would let a parent attribute the child's usage
        # to the parent's model — treat it as unidentified instead.
        models = {
            m.model_name for m in self.usage_to_metrics.values() if m.model_name != ""
        }
        if len(models) == 1:
            model_name = models.pop()
            if model_name != "default":
                total_metrics.model_name = model_name
                if total_metrics.accumulated_token_usage is not None:
                    total_metrics.accumulated_token_usage.model = model_name
        return total_metrics

    def get_metrics_for_usage(self, usage_id: str) -> Metrics:
        if usage_id not in self.usage_to_metrics:
            raise Exception(f"LLM usage does not exist {usage_id}")

        return self.usage_to_metrics[usage_id]

    def register_llm(self, event: RegistryEvent):
        # Listen for LLM registrations and track the metrics of their session:
        # the one the registry keeps for the usage ID, else the LLM's own.
        usage_id = event.llm.usage_id
        session = event.session if event.session is not None else event.llm.session

        # Usage costs exist but have not been restored yet
        if (
            usage_id in self.usage_to_metrics
            and usage_id not in self._restored_usage_ids
        ):
            session.restore_metrics(self.usage_to_metrics[usage_id])
            self._restored_usage_ids.add(usage_id)

        # Usage is new, track its metrics
        if usage_id not in self.usage_to_metrics:
            self.usage_to_metrics[usage_id] = session.metrics
