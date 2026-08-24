"""Client-side estimation of per-call prompt token composition."""

from typing import Any

from litellm import ChatCompletionToolParam
from litellm.utils import token_counter

from openhands.sdk.llm.utils.metrics import PromptComposition
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# token_counter requires at least one message when tools are passed, so tool
# schema tokens are measured as the marginal cost over an empty probe message.
_TOOLS_PROBE_MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": ""}]


def compute_prompt_composition(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[ChatCompletionToolParam] | None = None,
    custom_tokenizer: Any = None,
) -> PromptComposition | None:
    """Estimate prompt tokens per component for a single LLM call.

    Args:
        model: Model name used to pick the tokenizer.
        messages: Final OpenAI chat-format messages for the call.
        tools: Final OpenAI-format tool schemas for the call, if sent as tools
            (when tool schemas are rendered into the prompt text instead, pass
            None so they are not double-counted).
        custom_tokenizer: Optional LiteLLM custom tokenizer override.

    Returns:
        A PromptComposition snapshot, or None when the model's tokenizer is
        unavailable (composition recording is best-effort).
    """

    def count(
        msgs: list[dict[str, Any]], tool_params: list[ChatCompletionToolParam] | None
    ) -> int:
        return int(
            token_counter(
                model=model,
                messages=msgs,
                tools=tool_params,
                custom_tokenizer=custom_tokenizer,
                # Avoid a GET request per http(s) image URL while counting.
                use_default_image_token_count=True,
            )
        )

    system_messages = [m for m in messages if m.get("role") == "system"]
    conversation = [m for m in messages if m.get("role") != "system"]

    try:
        tool_tokens = 0
        if tools:
            tool_tokens = count(_TOOLS_PROBE_MESSAGES, tools) - count(
                _TOOLS_PROBE_MESSAGES, None
            )
        return PromptComposition(
            model=model,
            system_prompt_tokens=count(system_messages, None) if system_messages else 0,
            tool_tokens=tool_tokens,
            history_tokens=count(conversation[:-1], None)
            if len(conversation) > 1
            else 0,
            latest_message_tokens=count(conversation[-1:], None) if conversation else 0,
        )
    except Exception:
        logger.debug("Prompt composition counting failed for %s", model, exc_info=True)
        return None
