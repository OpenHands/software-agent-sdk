import json
from abc import abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

from litellm import ChatCompletionMessageToolCall, ResponseFunctionToolCall
from litellm.types.responses.main import (
    GenericResponseOutputItem,
    OutputFunctionToolCall,
)
from litellm.types.utils import Message as LiteLLMMessage
from openai.types.responses.response_output_message import ResponseOutputMessage
from openai.types.responses.response_reasoning_item import ResponseReasoningItem
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openhands.sdk.logger import get_logger
from openhands.sdk.utils import DEFAULT_TEXT_CONTENT_LIMIT, maybe_truncate
from openhands.sdk.utils.deprecation import warn_deprecated


logger = get_logger(__name__)


class MessageToolCall(BaseModel):
    """Transport-agnostic tool call representation.

    One canonical id is used for linking across actions/observations and
    for Responses function_call_output call_id.
    """

    id: str = Field(..., description="Canonical tool call id")
    name: str = Field(..., description="Tool/function name")
    arguments: str = Field(..., description="JSON string of arguments")
    origin: Literal["completion", "responses"] = Field(
        ..., description="Originating API family"
    )

    @classmethod
    def from_chat_tool_call(
        cls, tool_call: ChatCompletionMessageToolCall
    ) -> "MessageToolCall":
        """Create a MessageToolCall from a Chat Completions tool call."""
        if not tool_call.type == "function":
            raise ValueError(
                f"Unsupported tool call type for {tool_call=}, expected 'function' "
                f"not {tool_call.type}'"
            )
        if tool_call.function is None:
            raise ValueError(f"tool_call.function is None for {tool_call=}")
        if tool_call.function.name is None:
            raise ValueError(f"tool_call.function.name is None for {tool_call=}")

        return cls(
            id=tool_call.id,
            name=tool_call.function.name,
            arguments=tool_call.function.arguments,
            origin="completion",
        )

    @classmethod
    def from_responses_function_call(
        cls, item: ResponseFunctionToolCall | OutputFunctionToolCall
    ) -> "MessageToolCall":
        """Create a MessageToolCall from a typed OpenAI Responses function_call item.

        Note: OpenAI Responses function_call.arguments is already a JSON string.
        """
        call_id = item.call_id or item.id or ""
        name = item.name or ""
        arguments_str = item.arguments or ""

        if not call_id:
            raise ValueError(f"Responses function_call missing call_id/id: {item!r}")
        if not name:
            raise ValueError(f"Responses function_call missing name: {item!r}")

        return cls(
            id=str(call_id),
            name=str(name),
            arguments=arguments_str,
            origin="responses",
        )

    def to_chat_dict(self) -> dict[str, Any]:
        """Serialize to OpenAI Chat Completions tool_calls format."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments,
            },
        }

    def to_responses_dict(self) -> dict[str, Any]:
        """Serialize to OpenAI Responses 'function_call' input item format."""
        # Responses requires ids to begin with 'fc'
        resp_id = self.id if str(self.id).startswith("fc") else f"fc_{self.id}"
        # Responses requires arguments to be a JSON string
        args_str = (
            self.arguments
            if isinstance(self.arguments, str)
            else json.dumps(self.arguments)
        )
        return {
            "type": "function_call",
            "id": resp_id,
            "call_id": resp_id,
            "name": self.name,
            "arguments": args_str,
        }


class ThinkingBlock(BaseModel):
    """Anthropic thinking block for extended thinking feature.

    This represents the raw thinking blocks returned by Anthropic models
    when extended thinking is enabled. These blocks must be preserved
    and passed back to the API for tool use scenarios.
    """

    type: Literal["thinking"] = "thinking"
    thinking: str = Field(..., description="The thinking content")
    signature: str | None = Field(
        default=None, description="Cryptographic signature for the thinking block"
    )


class RedactedThinkingBlock(BaseModel):
    """Redacted thinking block for previous responses without extended thinking.

    This is used as a placeholder for assistant messages that were generated
    before extended thinking was enabled.
    """

    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str = Field(..., description="The redacted thinking content")


class ReasoningItemModel(BaseModel):
    """OpenAI Responses reasoning item (non-stream, subset we consume).

    Do not log or render encrypted_content.
    """

    id: str | None = Field(default=None)
    summary: list[str] = Field(default_factory=list)
    content: list[str] | None = Field(default=None)
    encrypted_content: str | None = Field(default=None)
    status: str | None = Field(default=None)


class BaseContent(BaseModel):
    cache_prompt: bool = False

    @abstractmethod
    def to_llm_dict(self) -> list[dict[str, str | dict[str, str]]]:
        """Convert to LLM API format. Always returns a list of dictionaries.

        Subclasses should implement this method to return a list of dictionaries,
        even if they only have a single item.
        """


class TextContent(BaseContent):
    type: Literal["text"] = "text"
    text: str
    # We use populate_by_name since mcp.types.TextContent
    # alias meta -> _meta, but .model_dumps() will output "meta"
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", populate_by_name=True
    )

    def to_llm_dict(self) -> list[dict[str, str | dict[str, str]]]:
        """Convert to LLM API format."""
        data: dict[str, str | dict[str, str]] = {
            "type": self.type,
            "text": self.text,
        }
        if self.cache_prompt:
            data["cache_control"] = {"type": "ephemeral"}
        return [data]


class ImageContent(BaseContent):
    type: Literal["image"] = "image"
    image_urls: list[str]

    def to_llm_dict(self) -> list[dict[str, str | dict[str, str]]]:
        """Convert to LLM API format."""
        images: list[dict[str, str | dict[str, str]]] = []
        for url in self.image_urls:
            images.append({"type": "image_url", "image_url": {"url": url}})
        if self.cache_prompt and images:
            images[-1]["cache_control"] = {"type": "ephemeral"}
        return images


class Message(BaseModel):
    # NOTE: this is not the same as EventSource
    # These are the roles in the LLM's APIs
    role: Literal["user", "system", "assistant", "tool"]
    content: Sequence[TextContent | ImageContent] = Field(default_factory=list)
    # - tool calls (from LLM)
    tool_calls: list[MessageToolCall] | None = None
    # - tool execution result (to LLM)
    tool_call_id: str | None = None
    name: str | None = None  # name of the tool
    # reasoning content (from reasoning models like o1, Claude thinking, DeepSeek R1)
    reasoning_content: str | None = Field(
        default=None,
        description="Intermediate reasoning/thinking content from reasoning models",
    )
    # Anthropic-specific thinking blocks (not normalized by LiteLLM)
    thinking_blocks: Sequence[ThinkingBlock | RedactedThinkingBlock] = Field(
        default_factory=list,
        description="Raw Anthropic thinking blocks for extended thinking feature",
    )
    # OpenAI Responses reasoning item (when provided via Responses API output)
    responses_reasoning_item: ReasoningItemModel | None = Field(
        default=None,
        description="OpenAI Responses reasoning item from model output",
    )

    # Deprecated fields that were moved to to_chat_dict() parameters.
    # These fields are ignored but accepted for backward compatibility.
    # REMOVE_AT: 1.12.0 - Remove this list and the _handle_deprecated_fields validator
    _DEPRECATED_FIELDS: ClassVar[tuple[str, ...]] = (
        "cache_enabled",
        "vision_enabled",
        "function_calling_enabled",
        "force_string_serializer",
        "send_reasoning_content",
    )

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _handle_deprecated_fields(cls, data: Any) -> Any:
        """Handle deprecated fields by emitting warnings and removing them.

        REMOVE_AT: 1.12.0 - Remove this validator along with _DEPRECATED_FIELDS
        """
        if not isinstance(data, dict):
            return data

        deprecated_found = [f for f in cls._DEPRECATED_FIELDS if f in data]
        for field in deprecated_found:
            warn_deprecated(
                f"Message.{field}",
                deprecated_in="1.9.1",
                removed_in="1.12.0",
                details=(
                    f"The '{field}' field has been removed from Message. "
                    "Pass it as a parameter to to_chat_dict() instead, or use "
                    "LLM.format_messages_for_llm() which handles this automatically."
                ),
                stacklevel=4,  # Adjust for validator call depth
            )
            # Remove the deprecated field so Pydantic doesn't complain
            del data[field]

        return data

    @property
    def contains_image(self) -> bool:
        return any(isinstance(content, ImageContent) for content in self.content)

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_content(cls, v: Any) -> Sequence[TextContent | ImageContent] | Any:
        # Accept None → []
        if v is None:
            return []
        # Accept a single string → [TextContent(...)]
        if isinstance(v, str):
            return [TextContent(text=v)]
        return v

    def to_chat_dict(
        self,
        *,
        cache_enabled: bool,
        vision_enabled: bool,
        function_calling_enabled: bool,
        force_string_serializer: bool,
        send_reasoning_content: bool,
    ) -> dict[str, Any]:
        """Serialize message for OpenAI Chat Completions.

        Args:
            cache_enabled: Whether prompt caching is active.
            vision_enabled: Whether vision/image processing is enabled.
            function_calling_enabled: Whether native function calling is enabled.
            force_string_serializer: Force string serializer instead of list format.
            send_reasoning_content: Whether to include reasoning_content in output.

        Chooses the appropriate content serializer and then injects threading keys:
        - Assistant tool call turn: role == "assistant" and self.tool_calls
        - Tool result turn: role == "tool" and self.tool_call_id (with name)
        """
        if not force_string_serializer and (
            cache_enabled or vision_enabled or function_calling_enabled
        ):
            message_dict = self._list_serializer(vision_enabled=vision_enabled)
        else:
            # some providers, like HF and Groq/llama, don't support a list here, but a
            # single string
            message_dict = self._string_serializer()

        # Assistant function_call(s)
        if self.role == "assistant" and self.tool_calls:
            message_dict["tool_calls"] = [tc.to_chat_dict() for tc in self.tool_calls]
            self._remove_content_if_empty(message_dict)

        # Tool result (observation) threading
        if self.role == "tool" and self.tool_call_id is not None:
            assert self.name is not None, (
                "name is required when tool_call_id is not None"
            )
            message_dict["tool_call_id"] = self.tool_call_id
            message_dict["name"] = self.name

        # Required for model like kimi-k2-thinking
        if send_reasoning_content and self.reasoning_content:
            message_dict["reasoning_content"] = self.reasoning_content

        return message_dict

    def _string_serializer(self) -> dict[str, Any]:
        # convert content to a single string
        content = "\n".join(
            item.text for item in self.content if isinstance(item, TextContent)
        )
        if self.role == "tool":
            content = self._maybe_truncate_tool_text(content)
        message_dict: dict[str, Any] = {"content": content, "role": self.role}

        # tool call keys are added in to_chat_dict to centralize behavior
        return message_dict

    def _list_serializer(self, *, vision_enabled: bool) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        role_tool_with_prompt_caching = False

        # Add thinking blocks first (for Anthropic extended thinking)
        # Only add thinking blocks for assistant messages
        thinking_blocks_dicts = []
        if self.role == "assistant":
            thinking_blocks = list(
                self.thinking_blocks
            )  # Copy to avoid modifying original
            for thinking_block in thinking_blocks:
                thinking_dict = thinking_block.model_dump()
                thinking_blocks_dicts.append(thinking_dict)

        for item in self.content:
            # All content types now return list[dict[str, Any]]
            item_dicts = item.to_llm_dict()

            if self.role == "tool" and item_dicts:
                for d in item_dicts:
                    text_val = d.get("text")
                    if d.get("type") == "text" and isinstance(text_val, str):
                        d["text"] = self._maybe_truncate_tool_text(text_val)

            # We have to remove cache_prompt for tool content and move it up to the
            # message level
            # See discussion here for details: https://github.com/BerriAI/litellm/issues/6422#issuecomment-2438765472
            if self.role == "tool" and item.cache_prompt:
                role_tool_with_prompt_caching = True
                for d in item_dicts:
                    d.pop("cache_control", None)

            # Handle vision-enabled filtering for ImageContent
            if isinstance(item, ImageContent) and vision_enabled:
                content.extend(item_dicts)
            elif not isinstance(item, ImageContent):
                # Add non-image content (TextContent, etc.)
                content.extend(item_dicts)

        message_dict: dict[str, Any] = {"content": content, "role": self.role}
        if role_tool_with_prompt_caching:
            message_dict["cache_control"] = {"type": "ephemeral"}

        if thinking_blocks_dicts:
            message_dict["thinking_blocks"] = thinking_blocks_dicts

        # tool call keys are added in to_chat_dict to centralize behavior
        return message_dict

    def _remove_content_if_empty(self, message_dict: dict[str, Any]) -> None:
        """Remove empty text content entries from assistant tool-call messages.

        Mutates the provided message_dict in-place:
        - If content is a string of only whitespace, drop the 'content' key
        - If content is a list, remove any text items with empty text; if the list
          becomes empty, drop the 'content' key
        """
        if "content" not in message_dict:
            return

        content = message_dict["content"]

        if isinstance(content, str):
            if content.strip() == "":
                message_dict.pop("content", None)
            return

        if isinstance(content, list):
            normalized: list[Any] = []
            for item in content:
                if not isinstance(item, dict):
                    normalized.append(item)
                    continue

                if item.get("type") == "text":
                    text_value = item.get("text", "")
                    if isinstance(text_value, str):
                        if text_value.strip() == "":
                            continue
                    else:
                        raise ValueError(
                            f"Text content item has non-string text value: "
                            f"{text_value!r}"
                        )

                normalized.append(item)

            if normalized:
                message_dict["content"] = normalized
            else:
                message_dict.pop("content", None)
            return

        # Any other content shape is left as-is

    def to_responses_value(self, *, vision_enabled: bool) -> str | list[dict[str, Any]]:
        """Return serialized form.

        Either an instructions string (for system) or input items (for other roles)."""
        if self.role == "system":
            parts: list[str] = []
            for c in self.content:
                if isinstance(c, TextContent) and c.text:
                    parts.append(c.text)
            return "\n".join(parts)
        return self.to_responses_dict(vision_enabled=vision_enabled)

    def to_responses_dict(self, *, vision_enabled: bool) -> list[dict[str, Any]]:
        """Serialize message for OpenAI Responses (input parameter).

        Produces a list of "input" items for the Responses API:
        - system: returns [], system content is expected in 'instructions'
        - user: one 'message' item with content parts -> input_text / input_image
        (when vision enabled)
        - assistant: emits prior assistant content as input_text,
        and function_call items for tool_calls
        - tool: emits function_call_output items (one per TextContent)
        with matching call_id
        """
        items: list[dict[str, Any]] = []

        if self.role == "system":
            return items

        if self.role == "user":
            content_items: list[dict[str, Any]] = []
            for c in self.content:
                if isinstance(c, TextContent):
                    content_items.append({"type": "input_text", "text": c.text})
                elif isinstance(c, ImageContent) and vision_enabled:
                    for url in c.image_urls:
                        content_items.append(
                            {"type": "input_image", "image_url": url, "detail": "auto"}
                        )
            items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": content_items
                    or [
                        {
                            "type": "input_text",
                            "text": "",
                        }
                    ],
                }
            )
            return items

        if self.role == "assistant":
            # Include prior turn's reasoning item exactly as received (if any)
            # Send reasoning first, followed by content and tool calls
            if self.responses_reasoning_item is not None:
                ri = self.responses_reasoning_item
                # Only send back if we have an id; required by the param schema
                if ri.id is not None:
                    reasoning_item: dict[str, Any] = {
                        "type": "reasoning",
                        "id": ri.id,
                        # Always include summary exactly as received (can be empty)
                        "summary": [
                            {"type": "summary_text", "text": s}
                            for s in (ri.summary or [])
                        ],
                    }
                    # Optional content passthrough
                    if ri.content:
                        reasoning_item["content"] = [
                            {"type": "reasoning_text", "text": t} for t in ri.content
                        ]
                    # Optional fields as received
                    if ri.encrypted_content:
                        reasoning_item["encrypted_content"] = ri.encrypted_content
                    if ri.status:
                        reasoning_item["status"] = ri.status
                    items.append(reasoning_item)

            # Emit prior assistant content as a single message item using output_text
            content_items: list[dict[str, Any]] = []
            for c in self.content:
                if isinstance(c, TextContent) and c.text:
                    content_items.append({"type": "output_text", "text": c.text})
            if content_items:
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": content_items,
                    }
                )
            # Emit assistant tool calls so subsequent function_call_output
            # can match call_id
            if self.tool_calls:
                for tc in self.tool_calls:
                    items.append(tc.to_responses_dict())

            return items

        if self.role == "tool":
            if self.tool_call_id is not None:
                # Responses requires function_call_output.call_id
                # to match a previous function_call id
                resp_call_id = (
                    self.tool_call_id
                    if str(self.tool_call_id).startswith("fc")
                    else f"fc_{self.tool_call_id}"
                )
                for c in self.content:
                    if isinstance(c, TextContent):
                        output_text = self._maybe_truncate_tool_text(c.text)
                        items.append(
                            {
                                "type": "function_call_output",
                                "call_id": resp_call_id,
                                "output": output_text,
                            }
                        )
                    elif isinstance(c, ImageContent) and vision_enabled:
                        for url in c.image_urls:
                            items.append(
                                {
                                    "type": "function_call_output",
                                    "call_id": resp_call_id,
                                    "output": [
                                        {
                                            "type": "input_image",
                                            "image_url": url,
                                            "detail": "auto",
                                        }
                                    ],
                                }
                            )
            return items

        return items

    def _maybe_truncate_tool_text(self, text: str) -> str:
        if not text or len(text) <= DEFAULT_TEXT_CONTENT_LIMIT:
            return text
        logger.warning(
            "Tool TextContent text length (%s) exceeds limit (%s), truncating",
            len(text),
            DEFAULT_TEXT_CONTENT_LIMIT,
        )
        return maybe_truncate(text, DEFAULT_TEXT_CONTENT_LIMIT)

    @classmethod
    def from_llm_chat_message(cls, message: LiteLLMMessage) -> "Message":
        """Convert a LiteLLMMessage (Chat Completions) to our Message class.

        Provider-agnostic mapping for reasoning:
        - Prefer `message.reasoning_content` if present (LiteLLM normalized field)
        - Extract `thinking_blocks` from content array (Anthropic-specific)
        """
        assert message.role != "function", "Function role is not supported"

        rc = getattr(message, "reasoning_content", None)
        thinking_blocks = getattr(message, "thinking_blocks", None)

        # Convert to list of ThinkingBlock or RedactedThinkingBlock
        if thinking_blocks is not None:
            thinking_blocks = [
                ThinkingBlock(**tb)
                if tb.get("type") == "thinking"
                else RedactedThinkingBlock(**tb)
                for tb in thinking_blocks
            ]
        else:
            thinking_blocks = []

        tool_calls = None

        if message.tool_calls:
            # Validate tool calls - filter out non-function types
            if any(tc.type != "function" for tc in message.tool_calls):
                logger.warning(
                    "LLM returned tool calls but some are not of type 'function' - "
                    "ignoring those"
                )

            function_tool_calls = [
                tc for tc in message.tool_calls if tc.type == "function"
            ]

            if len(function_tool_calls) > 0:
                tool_calls = [
                    MessageToolCall.from_chat_tool_call(tc)
                    for tc in function_tool_calls
                ]
            else:
                # If no function tool calls remain after filtering, raise an error
                raise ValueError(
                    "LLM returned tool calls but none are of type 'function'"
                )

        return Message(
            role=message.role,
            content=[TextContent(text=message.content)]
            if isinstance(message.content, str)
            else [],
            tool_calls=tool_calls,
            reasoning_content=rc,
            thinking_blocks=thinking_blocks,
        )

    @classmethod
    def from_llm_responses_output(
        cls,
        output: Any,
    ) -> "Message":
        """Convert OpenAI Responses API output items into a single assistant Message.

        Policy (non-stream):
        - Collect assistant text by concatenating output_text parts from message items
        - Normalize function_call items to MessageToolCall list
        """
        assistant_text_parts: list[str] = []
        tool_calls: list[MessageToolCall] = []
        responses_reasoning_item: ReasoningItemModel | None = None

        for item in output or []:
            if (
                isinstance(item, GenericResponseOutputItem)
                or isinstance(item, ResponseOutputMessage)
            ) and item.type == "message":
                for part in item.content or []:
                    if part.type == "output_text" and part.text:
                        assistant_text_parts.append(part.text)
            elif (
                isinstance(item, (OutputFunctionToolCall, ResponseFunctionToolCall))
                and item.type == "function_call"
            ):
                tc = MessageToolCall.from_responses_function_call(item)
                tool_calls.append(tc)
            elif isinstance(item, ResponseReasoningItem) and item.type == "reasoning":
                # Parse OpenAI typed Responses "reasoning" output item
                # (Pydantic BaseModel)
                rid = item.id
                summaries = item.summary or []
                contents = item.content or []
                enc = item.encrypted_content
                status = item.status

                summary_list: list[str] = [s.text for s in summaries]
                content_texts: list[str] = [c.text for c in contents]
                content_list: list[str] | None = content_texts or None

                responses_reasoning_item = ReasoningItemModel(
                    id=rid,
                    summary=summary_list,
                    content=content_list,
                    encrypted_content=enc,
                    status=status,
                )

        assistant_text = "\n".join(assistant_text_parts).strip()
        return Message(
            role="assistant",
            content=[TextContent(text=assistant_text)] if assistant_text else [],
            tool_calls=tool_calls or None,
            responses_reasoning_item=responses_reasoning_item,
        )


def content_to_str(contents: Sequence[TextContent | ImageContent]) -> list[str]:
    """Convert a list of TextContent and ImageContent to a list of strings.

    This is primarily used for display purposes.
    """
    text_parts = []
    for content_item in contents:
        if isinstance(content_item, TextContent):
            text_parts.append(content_item.text)
        elif isinstance(content_item, ImageContent):
            text_parts.append(f"[Image: {len(content_item.image_urls)} URLs]")
    return text_parts
