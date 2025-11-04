import warnings

from pydantic import AliasChoices, BaseModel, Field, PrivateAttr

from openhands.sdk.llm.llm_registry import RegistryEvent
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

SERVICE_TO_USAGE_DEPRECATION_MSG = (
    "ConversationStats.service_to_metrics is deprecated; use usage_to_metrics instead."
)
RESTORED_SERVICES_DEPRECATION_MSG = (
    "ConversationStats._restored_services is deprecated; "
    "use _restored_usage_ids instead."
)


class ConversationStats(BaseModel):
    """Track per-LLM usage metrics observed during conversations."""

    usage_to_metrics: dict[str, Metrics] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("usage_to_metrics", "service_to_metrics"),
        serialization_alias="usage_to_metrics",
        description="Active usage metrics tracked by the registry.",
    )

    _restored_usage_ids: set[str] = PrivateAttr(default_factory=set)

    @property
    def service_to_metrics(
        self,
    ) -> dict[str, Metrics]:  # pragma: no cover - compatibility shim
        warnings.warn(
            SERVICE_TO_USAGE_DEPRECATION_MSG,
            DeprecationWarning,
            stacklevel=2,
        )
        return self.usage_to_metrics

    @service_to_metrics.setter
    def service_to_metrics(
        self, value: dict[str, Metrics]
    ) -> None:  # pragma: no cover - compatibility shim
        warnings.warn(
            SERVICE_TO_USAGE_DEPRECATION_MSG,
            DeprecationWarning,
            stacklevel=2,
        )
        self.usage_to_metrics = value

    @property
    def _restored_services(self) -> set[str]:  # pragma: no cover - compatibility shim
        warnings.warn(
            RESTORED_SERVICES_DEPRECATION_MSG,
            DeprecationWarning,
            stacklevel=2,
        )
        return self._restored_usage_ids

    def get_combined_metrics(self) -> Metrics:
        total_metrics = Metrics()
        for metrics in self.usage_to_metrics.values():
            total_metrics.merge(metrics)
        return total_metrics

    def get_metrics_for_usage(self, usage_id: str) -> Metrics:
        if usage_id not in self.usage_to_metrics:
            raise Exception(f"LLM usage does not exist {usage_id}")

        return self.usage_to_metrics[usage_id]

    def get_metrics_for_service(
        self, service_id: str
    ) -> Metrics:  # pragma: no cover - compatibility shim
        warnings.warn(
            SERVICE_TO_USAGE_DEPRECATION_MSG,
            DeprecationWarning,
            stacklevel=2,
        )
        return self.get_metrics_for_usage(service_id)

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
