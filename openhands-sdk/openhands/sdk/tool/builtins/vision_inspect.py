from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


VISION_INSPECT_TOOL_NAME = "inspect_image_with_vision"
VISION_PROFILE_USAGE_PREFIX = "vision-profile"


class VisionInspectAction(Action):
    """Action for asking a vision model about an attached image."""

    image_index: int = Field(
        ge=0,
        description=(
            "Zero-based index of the image to inspect from the latest user message."
        ),
    )
    question: str = Field(
        min_length=1,
        description="Question to ask the vision model about the selected image.",
    )
    profile_name: str | None = Field(
        default=None,
        description=(
            "Optional saved vision-capable LLM profile name. If omitted, the first "
            "available vision profile listed in the tool description is used."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Inspect image with vision: ", style="bold magenta")
        content.append(f"image {self.image_index}")
        if self.profile_name:
            content.append(f" via {self.profile_name}")
        content.append("\nQuestion: ", style="bold")
        content.append(self.question)
        return content


class VisionInspectObservation(Observation):
    """Observation returned after a vision model inspects an image."""

    image_index: int = Field(description="Image index that was inspected.")
    question: str = Field(description="Question asked of the vision model.")
    profile_name: str | None = Field(
        default=None, description="Vision-capable profile used for inspection."
    )
    model: str | None = Field(
        default=None, description="Vision-capable model used for inspection."
    )
    base_url: str | None = Field(
        default=None, description="Vision-capable LLM endpoint used for inspection."
    )
    answer: str | None = Field(
        default=None, description="Text answer returned by the vision model."
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Failed to inspect image", style="bold red")
        else:
            content.append("Inspected image with vision", style="bold green")
        content.append(f": image {self.image_index}")
        if self.profile_name:
            content.append(f" via {self.profile_name}")
        if self.answer:
            content.append("\n")
            content.append(self.answer)
        return content


def _candidate_vision_profiles() -> list[str]:
    """Return saved profile names that appear to support vision."""
    profiles: list[str] = []
    store = LLMProfileStore()
    for summary in store.list_summaries():
        name = summary.get("name")
        model = summary.get("model")
        if not isinstance(name, str) or not isinstance(model, str):
            continue
        try:
            llm = LLM(model=model, base_url=summary.get("base_url"))
        except Exception:
            continue
        if llm.vision_is_active():
            profiles.append(name)
            continue
        try:
            llm = store.load(name)
        except Exception:
            continue
        if llm.vision_is_active():
            profiles.append(name)
    return sorted(profiles)


def has_vision_profile_available() -> bool:
    """Return True when at least one saved profile appears vision-capable."""
    return bool(_candidate_vision_profiles())


def _format_profiles(profile_names: Sequence[str]) -> str:
    if not profile_names:
        return "- No saved vision-capable LLM profiles are currently available."
    return "\n".join(f"- {name}" for name in profile_names)


def _latest_user_image_urls(conversation: "LocalConversation") -> list[str]:
    from openhands.sdk.event import MessageEvent

    for event in reversed(conversation.state.events):
        if not isinstance(event, MessageEvent) or event.source != "user":
            continue
        urls: list[str] = []
        for content in event.llm_message.content:
            if isinstance(content, ImageContent):
                urls.extend(content.image_urls)
        if urls:
            return urls
    return []


class VisionInspectExecutor(ToolExecutor):
    def __init__(self, profile_names: Sequence[str]) -> None:
        self._profile_names = tuple(profile_names)

    def __call__(
        self,
        action: VisionInspectAction,
        conversation: "LocalConversation | None" = None,
    ) -> VisionInspectObservation:
        if conversation is None:
            return VisionInspectObservation.from_text(
                text="Cannot inspect images without an active conversation.",
                is_error=True,
                image_index=action.image_index,
                question=action.question,
                profile_name=action.profile_name,
            )

        profile_name = action.profile_name or (
            self._profile_names[0] if self._profile_names else None
        )
        if profile_name is None:
            return VisionInspectObservation.from_text(
                text="No vision-capable LLM profile is available.",
                is_error=True,
                image_index=action.image_index,
                question=action.question,
            )
        if profile_name not in self._profile_names:
            return VisionInspectObservation.from_text(
                text=(
                    f"Profile '{profile_name}' is not listed as a vision-capable "
                    "profile for this conversation."
                ),
                is_error=True,
                image_index=action.image_index,
                question=action.question,
                profile_name=profile_name,
            )

        image_urls = _latest_user_image_urls(conversation)
        if action.image_index >= len(image_urls):
            return VisionInspectObservation.from_text(
                text=(
                    f"Image index {action.image_index} is out of range. "
                    f"The latest user message has {len(image_urls)} image(s)."
                ),
                is_error=True,
                image_index=action.image_index,
                question=action.question,
                profile_name=profile_name,
            )

        try:
            vision_llm = conversation.get_or_create_profile_llm(
                profile_name=profile_name,
                usage_id=f"{VISION_PROFILE_USAGE_PREFIX}:{profile_name}",
            )
        except Exception as exc:
            return VisionInspectObservation.from_text(
                text=(
                    f"Failed to load vision profile '{profile_name}': "
                    f"{type(exc).__name__}: {exc}"
                ),
                is_error=True,
                image_index=action.image_index,
                question=action.question,
                profile_name=profile_name,
            )

        if not vision_llm.vision_is_active():
            return VisionInspectObservation.from_text(
                text=(
                    f"Profile '{profile_name}' loaded model '{vision_llm.model}', "
                    "which does not currently support vision."
                ),
                is_error=True,
                image_index=action.image_index,
                question=action.question,
                profile_name=profile_name,
                model=vision_llm.model,
            )

        messages = [
            Message(
                role="system",
                content=[
                    TextContent(
                        text=(
                            "Answer the user's question about the attached image. "
                            "Return concise text only."
                        )
                    )
                ],
            ),
            Message(
                role="user",
                content=[
                    TextContent(text=action.question),
                    ImageContent(image_urls=[image_urls[action.image_index]]),
                ],
            ),
        ]
        from openhands.sdk.agent.utils import make_llm_completion

        response = make_llm_completion(vision_llm, messages, tools=[])
        answer = next(
            (
                content.text
                for content in response.message.content
                if isinstance(content, TextContent)
            ),
            "",
        )
        if not answer:
            return VisionInspectObservation.from_text(
                text="The vision model returned no text answer.",
                is_error=True,
                image_index=action.image_index,
                question=action.question,
                profile_name=profile_name,
                model=vision_llm.model,
                base_url=vision_llm.base_url,
            )

        return VisionInspectObservation.from_text(
            text=answer,
            image_index=action.image_index,
            question=action.question,
            profile_name=profile_name,
            model=vision_llm.model,
            base_url=vision_llm.base_url,
            answer=answer,
        )


_DESCRIPTION_TEMPLATE = (
    "Ask a saved vision-capable LLM profile to inspect an image from the latest "
    "user message and return a text answer.\n\n"
    "Use this when the current model cannot understand images, the latest user "
    "message includes an image, and visual details are needed to answer. The "
    "current model should pass the image_index shown in the user message and a "
    "specific question for the vision model. The cost of this vision model call "
    "is tracked in the same conversation stats.\n\n"
    "Available vision-capable profiles:\n"
    "{profiles}"
)


class VisionInspectTool(ToolDefinition[VisionInspectAction, VisionInspectObservation]):
    """Tool for one-off image inspection through a saved vision profile."""

    name: ClassVar[str] = VISION_INSPECT_TOOL_NAME

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("VisionInspectTool doesn't accept parameters")

        profile_names = _candidate_vision_profiles()
        if not profile_names:
            return []

        return [
            cls(
                description=_DESCRIPTION_TEMPLATE.format(
                    profiles=_format_profiles(profile_names)
                ),
                action_type=VisionInspectAction,
                observation_type=VisionInspectObservation,
                executor=VisionInspectExecutor(profile_names),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
            )
        ]
