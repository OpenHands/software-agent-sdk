"""Built-in tool that classifies the current task and switches LLM profile.

The tool reads the *active meta-profile* (see
:class:`~openhands.sdk.llm.meta_profile_store.MetaProfile`), asks the
meta-profile's ``classifier_model`` to categorize the current task using the
last few conversation messages, and switches the conversation to the LLM
profile mapped to the chosen class (falling back to ``default_model``).
"""

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.llm.meta_profile_store import MetaProfile, MetaProfileStore
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.registry import register_tool
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


logger = get_logger(__name__)

# Number of trailing conversation messages shown to the classifier.
_RECENT_MESSAGE_LIMIT = 6


class ClassifyAndSwitchLLMAction(Action):
    """Trigger classification of the current task and switch the LLM profile."""

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Classify task and switch LLM", style="bold magenta")
        return content


class ClassifyAndSwitchLLMObservation(Observation):
    """Result of classifying the task and switching the LLM profile."""

    chosen_class: str | None = Field(
        default=None,
        description="Description of the matched class, or None when the "
        "default model was used.",
    )
    model: str | None = Field(
        default=None,
        description="Name of the LLM profile that was activated.",
    )
    active_model: str | None = Field(
        default=None,
        description="Model configured by the activated profile, when available.",
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Failed to classify and switch LLM", style="bold red")
        else:
            content.append("Classified task and switched LLM", style="bold green")
        if self.model:
            content.append(f": {self.model}")
        if self.active_model:
            content.append(f" ({self.active_model})")
        if self.chosen_class:
            content.append("\nMatched class: ", style="bold")
            content.append(self.chosen_class)
        return content


def build_classifier_prompt(meta: MetaProfile) -> str:
    """Build the classifier system prompt listing the meta-profile classes."""
    lines = [
        "You are a model-routing classifier. Based on the recent conversation, "
        "pick the single category that best describes the current task.",
        "",
        "Categories:",
    ]
    for i, cls in enumerate(meta.classes, start=1):
        lines.append(f"{i}. {cls.description}")
    lines.append("")
    lines.append(
        "Respond with ONLY the number of the best matching category. "
        "If none of the categories clearly apply, respond with 0."
    )
    return "\n".join(lines)


def parse_class_index(text: str, num_classes: int) -> int:
    """Parse the classifier reply into a class index.

    Returns 0 (use ``default_model``) when no in-range integer is found.
    """
    match = re.search(r"-?\d+", text)
    if match is None:
        return 0
    index = int(match.group())
    if 1 <= index <= num_classes:
        return index
    return 0


def _recent_messages_text(
    conversation: "LocalConversation", limit: int = _RECENT_MESSAGE_LIMIT
) -> str:
    """Return a transcript of the last ``limit`` conversation messages.

    Only ``MessageEvent`` is included on purpose: user/assistant messages are
    the task signal the classifier needs. Action/observation events (tool calls,
    outputs) are deliberately excluded — they are noisy, can be large, and don't
    describe the task any better than the surrounding messages.
    """
    from openhands.sdk.event.llm_convertible.message import MessageEvent
    from openhands.sdk.llm import content_to_str

    messages: list[str] = []
    for event in conversation.state.events:
        if not isinstance(event, MessageEvent):
            continue
        text = "\n".join(content_to_str(event.llm_message.content)).strip()
        if text:
            messages.append(f"{event.source}: {text}")
    return "\n".join(messages[-limit:])


class ClassifyAndSwitchLLMExecutor(ToolExecutor):
    def __init__(
        self,
        meta_profile_store: MetaProfileStore,
        active_meta_profile: str | None = None,
    ) -> None:
        # Resolve the meta-profile lazily (at invocation), not at construction,
        # so a missing/renamed file under ~/.openhands/meta-profiles produces a
        # tool error instead of breaking conversation startup.
        self._store = meta_profile_store
        self._active_meta_profile = active_meta_profile

    def _resolve_meta_profile(self) -> MetaProfile:
        """Resolve the active meta-profile, falling back to the first available.

        Raises:
            FileNotFoundError: If no meta-profile can be resolved.
            ValueError: If the resolved meta-profile is invalid.
        """
        name = self._active_meta_profile
        if not name:
            available = self._store.list()
            if not available:
                raise FileNotFoundError(
                    "No meta-profile is active and none are available in the "
                    "meta-profile store."
                )
            # ``list()`` is alphabetically sorted, so this is the
            # alphabetically-first meta-profile, not the most recently saved.
            name = available[0]
            logger.info(
                "No active meta-profile set; falling back to first available: %r",
                name,
            )
        return self._store.load(name)

    def __call__(
        self,
        action: ClassifyAndSwitchLLMAction,  # noqa: ARG002
        conversation: "LocalConversation | None" = None,
    ) -> ClassifyAndSwitchLLMObservation:
        from openhands.sdk.llm import Message, TextContent, content_to_str

        if conversation is None:
            return ClassifyAndSwitchLLMObservation.from_text(
                text="Cannot classify and switch LLM without an active conversation.",
                is_error=True,
            )

        try:
            meta = self._resolve_meta_profile()
        except (FileNotFoundError, ValueError) as exc:
            return ClassifyAndSwitchLLMObservation.from_text(
                text=f"Failed to resolve the active meta-profile: {exc}",
                is_error=True,
            )
        # 1) Load the classifier LLM (a saved profile), respecting at-rest cipher,
        #    and register it in the conversation's LLM registry under a stable
        #    usage id. Registration is what makes the classifier completion's
        #    tokens/cost flow into ``conversation.conversation_stats`` and count
        #    against ``max_budget_per_run`` — calling an unregistered LLM would
        #    spend off the books (mirrors the ``ask-agent-llm`` pattern). Caching
        #    by usage id means repeated routing calls reuse one metrics bucket.
        usage_id = f"classifier:{meta.classifier_model}"
        try:
            classifier_llm = conversation.llm_registry.get(usage_id)
        except KeyError:
            try:
                loaded = conversation._profile_store.load(
                    meta.classifier_model, cipher=conversation._cipher
                )
            except (FileNotFoundError, ValueError) as exc:
                return ClassifyAndSwitchLLMObservation.from_text(
                    text=(
                        f"Failed to load classifier profile "
                        f"'{meta.classifier_model}': {exc}"
                    ),
                    is_error=True,
                )
            classifier_llm = loaded.model_copy(update={"usage_id": usage_id})
            conversation.llm_registry.add(classifier_llm)

        # 2) Single classifier call over the recent conversation.
        transcript = _recent_messages_text(conversation) or "(no messages yet)"
        messages = [
            Message(
                role="system",
                content=[TextContent(text=build_classifier_prompt(meta))],
            ),
            Message(
                role="user",
                content=[
                    TextContent(
                        text=f"Recent conversation:\n{transcript}\n\nCategory number:"
                    )
                ],
            ),
        ]
        try:
            response = classifier_llm.completion(messages)
        except Exception as exc:
            return ClassifyAndSwitchLLMObservation.from_text(
                text=f"Classifier call failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )

        reply = "\n".join(content_to_str(response.message.content))
        index = parse_class_index(reply, len(meta.classes))

        # 3) Resolve the target profile (chosen class or default).
        if index == 0:
            target_profile = meta.default_model
            chosen_class = None
        else:
            chosen = meta.classes[index - 1]
            target_profile = chosen.model
            chosen_class = chosen.description

        # 4) Switch the conversation to the target profile.
        try:
            conversation.switch_profile(target_profile)
        except FileNotFoundError:
            return ClassifyAndSwitchLLMObservation.from_text(
                text=f"Target LLM profile '{target_profile}' was not found.",
                is_error=True,
                model=target_profile,
                chosen_class=chosen_class,
            )
        except Exception as exc:
            return ClassifyAndSwitchLLMObservation.from_text(
                text=(
                    f"Failed to switch to LLM profile '{target_profile}': "
                    f"{type(exc).__name__}: {exc}"
                ),
                is_error=True,
                model=target_profile,
                chosen_class=chosen_class,
            )

        active_model = conversation.agent.llm.model
        label = chosen_class or "default"
        return ClassifyAndSwitchLLMObservation.from_text(
            text=(
                f"Classified task as '{label}' and switched to LLM profile "
                f"'{target_profile}' (model '{active_model}'). "
                "Future agent steps will use this profile."
            ),
            chosen_class=chosen_class,
            model=target_profile,
            active_model=active_model,
        )


_DESCRIPTION = (
    "Classify the current task and switch this conversation to the most "
    "suitable saved LLM profile.\n\n"
    "Use this near the start of a task (or when the task changes) to route "
    "the work to the best model. A classifier model inspects the recent "
    "conversation and picks a category from the active meta-profile; the "
    "conversation then switches to that category's LLM profile. The switch "
    "takes effect on the next LLM call."
)


class ClassifyAndSwitchLLMTool(
    ToolDefinition[ClassifyAndSwitchLLMAction, ClassifyAndSwitchLLMObservation]
):
    """Tool that classifies the task and switches to a meta-profile's LLM."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        active_meta_profile: str | None = None,
        meta_profile_store: MetaProfileStore | None = None,
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError(
                "ClassifyAndSwitchLLMTool only accepts 'active_meta_profile' "
                "and 'meta_profile_store'."
            )

        # Meta-profile resolution is deferred to invocation time (see
        # ClassifyAndSwitchLLMExecutor) so user-managed files under
        # ~/.openhands/meta-profiles cannot break conversation startup; a
        # missing/invalid profile surfaces as a tool error instead.
        store = meta_profile_store or MetaProfileStore()
        return [
            cls(
                description=_DESCRIPTION,
                action_type=ClassifyAndSwitchLLMAction,
                observation_type=ClassifyAndSwitchLLMObservation,
                executor=ClassifyAndSwitchLLMExecutor(store, active_meta_profile),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


# Registered so it can be resolved from a ``Tool`` spec carrying the
# ``active_meta_profile`` param (the built-in ``include_default_tools`` path
# cannot pass params).
register_tool(ClassifyAndSwitchLLMTool.__name__, ClassifyAndSwitchLLMTool)
