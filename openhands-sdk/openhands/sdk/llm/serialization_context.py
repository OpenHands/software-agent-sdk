from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from openhands.sdk.llm.utils.model_features import get_features


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM


@dataclass(frozen=True, slots=True)
class LLMSerializationContext:
    """Serialization policy for converting Message objects to provider payloads.

    This exists to keep `Message` as a pure data model (“what was said”), while
    keeping provider/model/invocation-specific serialization decisions (“how we
    send it”) on the LLM side.

    A context is intended to be *computed once* for a given LLM instance and
    reused across all messages formatted by that LLM.
    """

    cache_enabled: bool
    vision_enabled: bool
    function_calling_enabled: bool
    force_string_serializer: bool
    send_reasoning_content: bool

    def use_list_serializer(self) -> bool:
        """Whether messages should be serialized using list content format."""

        # Use list serializer when any feature requires structured content,
        # unless the provider requires string-only content.
        return (not self.force_string_serializer) and (
            self.cache_enabled or self.vision_enabled or self.function_calling_enabled
        )

    def as_dict(self) -> dict[str, bool]:
        """Return a dict of fields.

        This is a convenience helper for tests and call-sites that need to tweak
        one field while inheriting defaults from an existing context.
        """

        return asdict(self)

    @classmethod
    def from_llm(cls, llm: LLM) -> LLMSerializationContext:
        """Build serialization context from an LLM instance.

        Import is guarded by TYPE_CHECKING to avoid runtime import cycles.
        """

        model_features = get_features(llm._model_name_for_capabilities())

        force_string_serializer = (
            llm.force_string_serializer
            if llm.force_string_serializer is not None
            else model_features.force_string_serializer
        )

        return cls(
            cache_enabled=llm.is_caching_prompt_active(),
            vision_enabled=llm.vision_is_active(),
            function_calling_enabled=llm.native_tool_calling,
            force_string_serializer=force_string_serializer,
            send_reasoning_content=model_features.send_reasoning_content,
        )
