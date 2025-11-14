from collections.abc import Sequence

from pydantic import field_validator, model_validator

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.message import Message
from openhands.sdk.llm.router.base import RouterLLM
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.tool import ToolDefinition


logger = get_logger(__name__)


class FallbackRouter(RouterLLM):
    """
    A RouterLLM implementation that provides fallback capability across multiple
    language models. When the first model fails due to rate limits, timeouts,
    or service unavailability, it automatically falls back to subsequent models.

    Similar to litellm's fallback approach, models are tried in the order provided.
    If all models fail, the exception from the last model is raised.

    Example:
        >>> primary = LLM(model="gpt-4", usage_id="primary")
        >>> fallback = LLM(model="gpt-3.5-turbo", usage_id="fallback")
        >>> router = FallbackRouter(
        ...     usage_id="fallback-router",
        ...     llms=[primary, fallback]
        ... )
        >>> # Will try models in order until one succeeds
        >>> response = router.completion(messages)
    """

    router_name: str = "fallback_router"
    llms: list[LLM]

    @model_validator(mode="before")
    @classmethod
    def _convert_llms_to_routing(cls, values: dict) -> dict:
        """Convert llms list to llms_for_routing dict for base class compatibility."""
        if "llms" in values and "llms_for_routing" not in values:
            llms = values["llms"]
            values["llms_for_routing"] = {f"llm_{i}": llm for i, llm in enumerate(llms)}
        return values

    @field_validator("llms")
    @classmethod
    def _validate_llms(cls, llms: list[LLM]) -> list[LLM]:
        """Ensure at least one LLM is provided."""
        if not llms:
            raise ValueError("FallbackRouter requires at least one LLM")
        return llms

    def select_llm(self, messages: list[Message]) -> str:  # noqa: ARG002
        """
        For fallback router, we always start with the first model.
        The fallback logic is implemented in the completion() method.
        """
        return "llm_0"

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        return_metrics: bool = False,
        add_security_risk_prediction: bool = False,
        **kwargs,
    ) -> LLMResponse:
        """
        Try models in order until one succeeds. Falls back to next model
        on retry-able exceptions (rate limits, timeouts, service errors).
        """
        last_exception = None

        for i, llm in enumerate(self.llms):
            is_last_model = i == len(self.llms) - 1

            try:
                logger.info(
                    f"FallbackRouter: Attempting completion with model "
                    f"{i + 1}/{len(self.llms)} ({llm.model}, usage_id={llm.usage_id})"
                )
                self.active_llm = llm

                response = llm.completion(
                    messages=messages,
                    tools=tools,
                    _return_metrics=return_metrics,
                    add_security_risk_prediction=add_security_risk_prediction,
                    **kwargs,
                )

                logger.info(
                    f"FallbackRouter: Successfully completed with model "
                    f"{llm.model} (usage_id={llm.usage_id})"
                )
                return response

            except Exception as e:
                last_exception = e
                logger.warning(
                    f"FallbackRouter: Model {llm.model} (usage_id={llm.usage_id}) "
                    f"failed with {type(e).__name__}: {str(e)}"
                )

                if is_last_model:
                    logger.error(
                        "FallbackRouter: All models failed. Raising last exception."
                    )
                    raise
                else:
                    logger.info(
                        "FallbackRouter: Falling back to model "
                        f"{i + 2}/{len(self.llms)}..."
                    )

        # This should never happen, but satisfy type checker
        assert last_exception is not None
        raise last_exception
