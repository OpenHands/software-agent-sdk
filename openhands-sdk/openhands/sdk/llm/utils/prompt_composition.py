"""Client-side estimation of per-call prompt token composition.

Offline analysis utility: it tokenizes logged request payloads after a run
(see ``scripts/prompt_composition_report.py``); nothing here runs on the
LLM call path.
"""

from typing import Any

from litellm import ChatCompletionToolParam
from litellm.utils import token_counter
from pydantic import BaseModel, Field

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# token_counter requires a messages argument when tools are passed, so tool
# schema tokens are measured as the marginal cost over an empty probe message
# (messages=[] would also work in litellm 1.84.1; the probe keeps the call
# shape explicit either way).
_TOOLS_PROBE_MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": ""}]


class PromptComposition(BaseModel):
    """Per-call decomposition of prompt tokens by component.

    Counts are client-side estimates computed offline from the logged
    request payload; the provider-reported ``TokenUsage`` remains
    authoritative. Each component is counted independently, so per-message
    framing overhead is included in every bucket and the components do not
    necessarily sum exactly to the provider-reported ``prompt_tokens``.
    Tool schema counts follow litellm's ``token_counter`` serialization
    convention for tools, which can differ from the provider's wire-format
    tokenization. The composition always uses litellm's generic counter so
    numbers stay comparable across models.
    """

    model: str = Field(default="")
    system_prompt_tokens: int = Field(
        default=0, ge=0, description="Estimated tokens in system messages"
    )
    tool_schema_tokens: int = Field(
        default=0, ge=0, description="Estimated tokens in tool schemas"
    )
    history_tokens: int = Field(
        default=0,
        ge=0,
        description="Estimated tokens in conversation history (all non-system "
        "messages except the latest one)",
    )
    latest_message_tokens: int = Field(
        default=0,
        ge=0,
        description="Estimated tokens in the latest observation/user message",
    )
    is_estimate: bool = Field(
        default=True,
        description="True when counts are client-side estimates rather than "
        "provider-reported usage",
    )
    response_id: str = Field(default="")


def compute_prompt_composition(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[ChatCompletionToolParam] | None = None,
    custom_tokenizer: Any = None,
) -> PromptComposition | None:
    """Estimate prompt tokens per component for a single logged LLM call.

    Args:
        model: Model name used to pick the tokenizer.
        messages: Final OpenAI chat-format messages for the call.
        tools: Final OpenAI-format tool schemas for the call, if sent as tools
            (when tool schemas are rendered into the prompt text instead, pass
            None so they are not double-counted).
        custom_tokenizer: Optional LiteLLM custom tokenizer override.

    Returns:
        A PromptComposition snapshot, or None when counting fails or returns
        no tokens at all (e.g. ``litellm.disable_token_counter``), since
        composition analysis is best-effort.

    Cost scales linearly with prompt size and stays small in absolute terms —
    measured ~31 ms for a ~100K-token prompt and ~61 ms for ~190K tokens
    (gpt-4o tokenizer, 19 tools), versus ~10-20 ms on typical agent-step
    payloads.
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
        tool_schema_tokens = 0
        if tools:
            tool_schema_tokens = count(_TOOLS_PROBE_MESSAGES, tools) - count(
                _TOOLS_PROBE_MESSAGES, None
            )
        composition = PromptComposition(
            model=model,
            system_prompt_tokens=count(system_messages, None) if system_messages else 0,
            tool_schema_tokens=tool_schema_tokens,
            history_tokens=count(conversation[:-1], None)
            if len(conversation) > 1
            else 0,
            latest_message_tokens=count(conversation[-1:], None) if conversation else 0,
        )
    except Exception:
        logger.debug("Prompt composition counting failed for %s", model, exc_info=True)
        return None

    if (messages or tools) and not (
        composition.system_prompt_tokens
        + composition.tool_schema_tokens
        + composition.history_tokens
        + composition.latest_message_tokens
    ):
        logger.debug(
            "Prompt composition counting returned zero tokens for %s; skipping record",
            model,
        )
        return None
    return composition


def responses_payload_to_chat_messages(
    instructions: str | None, input_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Convert a finalized Responses API payload into chat-format dicts.

    Used so offline prompt composition on the Responses path counts what the
    provider actually received (instructions + input items) while sharing the
    chat path's counting convention. Raises ValueError on unrecognized item
    types; callers treat any failure as "skip the composition record".

    Buckets follow the wire, not the logical prompt: in subscription mode the
    system prompt is folded into the first user message by the auth-layer
    transform, so those tokens land in ``history_tokens`` or
    ``latest_message_tokens`` rather than ``system_prompt_tokens``.
    """
    messages: list[dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    for item in input_items:
        messages.extend(_responses_item_to_chat(item))
    return messages


def _responses_item_to_chat(item: dict[str, Any]) -> list[dict[str, Any]]:
    item_type = item.get("type")
    if item_type is None and "role" in item and "content" in item:
        # Subscription mode normalizes message items to {"role", "content"}
        # without a "type" key (see transform_for_subscription).
        item_type = "message"
    if item_type == "message":
        content = item.get("content", "")
        if isinstance(content, str):
            return [{"role": item["role"], "content": content}]
        return [
            {
                "role": item["role"],
                "content": [_responses_content_part_to_chat(part) for part in content],
            }
        ]
    if item_type == "function_call":
        return [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": item.get("call_id", ""),
                        "type": "function",
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": item.get("arguments", ""),
                        },
                    }
                ],
            }
        ]
    if item_type == "function_call_output":
        output = item.get("output", "")
        if isinstance(output, list):
            output = [_responses_content_part_to_chat(part) for part in output]
        return [
            {
                "role": "tool",
                "tool_call_id": item.get("call_id", ""),
                "content": output,
            }
        ]
    if item_type == "reasoning":
        texts = [part.get("text", "") for part in item.get("summary", [])]
        texts += [part.get("text", "") for part in item.get("content", [])]
        encrypted = item.get("encrypted_content")
        if encrypted:
            texts.append(encrypted)
        return [{"role": "assistant", "content": "\n".join(texts)}]
    raise ValueError(f"Unrecognized Responses input item type: {item_type!r}")


def _responses_content_part_to_chat(part: dict[str, Any]) -> dict[str, Any]:
    part_type = part.get("type")
    if part_type in ("input_text", "output_text"):
        return {"type": "text", "text": part.get("text", "")}
    if part_type == "input_image":
        return {"type": "image_url", "image_url": {"url": part.get("image_url", "")}}
    raise ValueError(f"Unrecognized Responses content part type: {part_type!r}")
