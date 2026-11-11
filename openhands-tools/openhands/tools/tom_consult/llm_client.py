"""Route ToM's structured requests through the SDK LLM boundary."""

from typing import Any, TypeVar

from litellm.types.utils import Message as LiteLLMMessage
from pydantic import BaseModel
from tom_swe.generation.generate import LLMClient
from tom_swe.generation.output_parsers import PydanticOutputParser

from openhands.sdk import LLM, Message, TextContent
from openhands.sdk.llm import LLMResponse
from openhands.sdk.llm.utils.openhands_provider import litellm_call_kwargs


T = TypeVar("T", bound=BaseModel)


class TomLLMClient(LLMClient):
    """Preserve ToM's client interface while using SDK provider handling."""

    def _llm(self, *, fallback: bool = False) -> LLM:
        base_url = litellm_call_kwargs(self.config.model, self.config.api_base)[
            "api_base"
        ]
        return LLM(
            model=self.config.fallback_model if fallback else self.config.model,
            api_key=self.config.api_key,
            base_url=base_url,
            usage_id="tom-consult",
        )

    @staticmethod
    def _system_message() -> Message:
        return Message(
            role="system",
            content=[
                TextContent(text="Analyze user context and return the requested JSON.")
            ],
        )

    @staticmethod
    def _message(message: dict[str, Any]) -> Message:
        content = message.get("content")
        if isinstance(content, list):
            # LiteLLM's response-message model rejects request content blocks.
            converted = Message.from_llm_chat_message(
                LiteLLMMessage(**{**message, "content": None})
            )
            converted.content = [
                TextContent(
                    text=part["text"],
                    cache_prompt="cache_control" in part,
                )
                for part in content
                if part.get("type") == "text"
            ]
            return converted
        return Message.from_llm_chat_message(LiteLLMMessage(**message))

    @staticmethod
    def _text(response: LLMResponse) -> str:
        text = "".join(
            part.text
            for part in response.message.content
            if isinstance(part, TextContent)
        )
        if not text:
            raise ValueError("Empty response from LLM")
        return text

    @staticmethod
    def _repair_prompt(content: str, instructions: str) -> str:
        return (
            "Reformat the following output to valid JSON. Return only JSON.\n"
            f"Output: {content}\n\n{instructions}"
        )

    async def call_structured_async(
        self, prompt: str, output_type: type[T], max_tokens: int | None = None
    ) -> T:
        """Generate and parse an asynchronous ToM analysis."""
        parser = PydanticOutputParser(output_type)
        instructions = parser.get_format_instructions()
        kwargs: dict[str, Any] = {}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = await self._llm().acompletion(
            messages=[
                self._system_message(),
                Message(
                    role="user",
                    content=[TextContent(text=f"{prompt}\n\n{instructions}")],
                ),
            ],
            response_format=output_type,
            **kwargs,
        )
        content = self._text(response)
        try:
            return parser.parse(content)
        except ValueError:
            repaired = await self.format_bad_output(content, instructions, output_type)
            return parser.parse(repaired)

    async def format_bad_output(
        self, ill_formed_output: str, format_instructions: str, output_type: type[T]
    ) -> str:
        """Keep ToM's repair fallback on the same SDK transport boundary."""
        response = await self._llm(fallback=True).acompletion(
            messages=[
                self._system_message(),
                Message(
                    role="user",
                    content=[
                        TextContent(
                            text=self._repair_prompt(
                                ill_formed_output, format_instructions
                            )
                        )
                    ],
                ),
            ],
            response_format=output_type,
        )
        return self._text(response)

    def call_structured_messages(
        self,
        messages: list[dict[str, Any]],
        output_type: type[T],
        max_tokens: int | None = None,
    ) -> T:
        """Generate and parse a synchronous ToM consultation."""
        parser = PydanticOutputParser(output_type)
        instructions = parser.get_format_instructions()
        sdk_messages = [self._message(message) for message in messages]
        if not sdk_messages or sdk_messages[0].role != "system":
            sdk_messages.insert(0, self._system_message())
        sdk_messages.append(
            Message(role="user", content=[TextContent(text=instructions)])
        )
        kwargs: dict[str, Any] = {}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = self._llm().completion(
            messages=sdk_messages,
            response_format=output_type,
            **kwargs,
        )
        content = self._text(response)
        try:
            return parser.parse(content)
        except ValueError:
            response = self._llm(fallback=True).completion(
                messages=[
                    self._system_message(),
                    Message(
                        role="user",
                        content=[
                            TextContent(text=self._repair_prompt(content, instructions))
                        ],
                    ),
                ],
                response_format=output_type,
            )
            return parser.parse(self._text(response))
