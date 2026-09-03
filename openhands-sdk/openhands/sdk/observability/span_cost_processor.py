"""Export-time rewrite of ``gen_ai.usage.cost`` on ended LLM spans.

lmnr's LiteLLM instrumentation ends its ``litellm.completion`` span inside the
completion call, ahead of ``Telemetry.on_response``, so attributes cannot be
attached afterwards. The authoritative cost is published instead from a litellm
success callback (see ``openhands.sdk.llm.llm._add_span_cost_callback``)
keyed by ``gen_ai.response.id``, and consumed here at ``on_end`` time. This
preserves the span's name, parent chain and all existing attributes (no double-
count, no trace-shape change).
"""

import threading
from typing import Any

from opentelemetry.attributes import BoundedAttributes
from opentelemetry.context.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class LLMSpanCostProcessor(SpanProcessor):
    def on_start(
        self,
        span: Span,  # noqa: ARG002
        parent_context: Context | None = None,  # noqa: ARG002
    ) -> None:
        return

    def on_end(self, span: ReadableSpan) -> None:
        try:
            response_id = (span.attributes or {}).get("gen_ai.response.id")
            if not response_id:
                return
            from openhands.sdk.llm.utils.telemetry import consume_llm_span_cost

            entry = consume_llm_span_cost(str(response_id))
            if entry is None:
                return
            cost, cache_read, cache_write = entry
            updates: dict[str, Any] = {}
            if cost:
                updates["gen_ai.usage.cost"] = float(cost)
            updates["gen_ai.usage.cache_read_input_tokens"] = int(cache_read or 0)
            updates["gen_ai.usage.cache_creation_input_tokens"] = int(cache_write or 0)

            # The exporter reads this ReadableSpan snapshot; replacing its
            # ``_attributes`` rewrites what every downstream processor/exporter sees.

            span._attributes = BoundedAttributes(
                attributes={**dict(span.attributes or {}), **updates}, immutable=False
            )
        except Exception:
            logger.debug("Failed to rewrite LLM span cost", exc_info=True)

    def shutdown(self) -> None:
        return

    def force_flush(
        self,
        timeout_millis: int = 30000,  # noqa: ARG002
    ) -> bool:
        return True


_installed = False
_install_lock = threading.Lock()


def install_llm_span_cost_processor() -> None:
    """Register the span-cost rewrite ahead of lmnr's own span processor."""
    global _installed
    if _installed:
        return
    with _install_lock:
        if _installed:
            return
        from lmnr.opentelemetry_lib.tracing import TracerWrapper

        provider = getattr(TracerWrapper.instance, "_tracer_provider", None)
        if provider is None:
            return

        processor = LLMSpanCostProcessor()
        active_processor = getattr(provider, "_active_span_processor", None)
        processors = getattr(active_processor, "_span_processors", None)
        if processors is not None and isinstance(processors, tuple):
            # Prepend so ``on_end`` runs before the LaminarSpanProcessor (needed
            # when lmnr runs in simple/non-batched export mode)..
            with active_processor._lock:  # type: ignore[attr-defined]
                active_processor._span_processors = (processor,) + processors  # type: ignore[method-assign,attr-defined]
        else:
            provider.add_span_processor(processor)
        _installed = True
