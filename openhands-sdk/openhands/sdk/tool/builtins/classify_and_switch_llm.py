"""Built-in tool that classifies the current task and switches LLM route.

The tool reads the *active meta-profile* (see
:class:`~openhands.sdk.llm.meta_profile_store.MetaProfile`), asks the
meta-profile's ``classifier_model`` to categorize the current task using the
last few conversation messages, and switches the conversation to the chosen
LLM profile or RouterLLM route. When the classifier produces no usable answer,
the tool fails loudly (returns an error observation) so the miss is visible and
retryable, instead of silently routing to a default model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, ClassVar, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.auth.openai import create_subscription_llm_from_config
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
    from openhands.sdk.llm.router.base import RouterLLM


logger = get_logger(__name__)

# Number of trailing conversation messages shown to the classifier.
_RECENT_MESSAGE_LIMIT = 6

_CLASSIFIER_SYSTEM_PREFIX = "You are a model-routing classifier."


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
        description="Description of the matched class, or None when no class "
        "matched and the tool returned an error.",
    )
    model: str | None = Field(
        default=None,
        description="Name of the LLM profile or RouterLLM route that was activated.",
    )
    active_model: str | None = Field(
        default=None,
        description=(
            "Underlying model configured by the activated target, when available."
        ),
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
        _CLASSIFIER_SYSTEM_PREFIX,
        "",
        "Based on the recent conversation, pick the single category that best "
        "describes the current task.",
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

    Returns 0 (no usable answer) when no in-range integer is found.
    """
    match = re.search(r"-?\d+", text)
    if match is None:
        return 0
    index = int(match.group())
    if 1 <= index <= num_classes:
        return index
    return 0


def render_direct_prompt(
    meta: MetaProfile,
    instance_text: str,
    *,
    model_table: str | None = None,
) -> str:
    """Render the direct-routing prompt body (without the system prefix).

    Only the minimal placeholders supported by meta-profiles are substituted.
    The ``_CLASSIFIER_SYSTEM_PREFIX`` is intentionally *not* prepended here so
    callers can place the rendered body in a ``user`` message while keeping the
    prefix in the ``system`` message — some providers (e.g. MiniMax) reject
    requests whose only turn is a ``system`` message with ``"chat content is
    empty"``.
    """
    prompt = meta.prompt_template or ""
    values = {
        "instance_text": instance_text,
        "model_table": (meta.model_table or "") if model_table is None else model_table,
    }

    def replace(match: re.Match[str]) -> str:
        return values.get(match.group(1), match.group(0))

    return re.sub(r"{{\s*(instance_text|model_table)\s*}}", replace, prompt).strip()


def build_classifier_messages(meta: MetaProfile, transcript: str) -> list[Message]:
    """Build the classifier request messages for ``meta``.

    Always returns a ``system`` message followed by a non-empty ``user``
    message. Providers such as MiniMax reject requests whose only turn is a
    ``system`` message (``"chat content is empty"``), so the task transcript
    and rendered prompt are placed in the ``user`` role for both routing
    modes.
    """
    if meta.prompt_template is None:
        return [
            Message(
                role="system",
                content=[TextContent(text=build_classifier_prompt(meta))],
            ),
            Message(
                role="user",
                content=[
                    TextContent(
                        text=(f"Recent conversation:\n{transcript}\n\nCategory number:")
                    )
                ],
            ),
        ]
    return [
        Message(
            role="system",
            content=[TextContent(text=_CLASSIFIER_SYSTEM_PREFIX)],
        ),
        Message(
            role="user",
            content=[TextContent(text=render_direct_prompt(meta, transcript))],
        ),
    ]


def _router_route_table(router: RouterLLM, meta: MetaProfile) -> str:
    class_descriptions = {cls.model: cls.description for cls in meta.classes}
    lines: list[str] = []
    for route in router.route_infos():
        details: list[str] = []
        if route.label and route.label != route.name:
            details.append(route.label)
        if route.model:
            details.append(f"model: {route.model}")
        if route.name in class_descriptions:
            details.append(class_descriptions[route.name])
        suffix = f" — {'; '.join(details)}" if details else ""
        lines.append(f"- {route.name}{suffix}")
    return "\n".join(lines)


def build_router_classifier_messages(
    meta: MetaProfile,
    transcript: str,
    router: RouterLLM,
) -> list[Message]:
    """Build classifier messages for choosing an active RouterLLM route."""
    route_table = _router_route_table(router, meta)
    if meta.prompt_template is not None:
        prompt = render_direct_prompt(meta, transcript, model_table=route_table)
    else:
        prompt = (
            "Based on the recent conversation, pick the single RouterLLM route "
            "that should handle the current task.\n\n"
            f"Routes:\n{route_table}\n\n"
            f"Recent conversation:\n{transcript}\n\n"
            "Respond with ONLY JSON: "
            '{"route": "<exact route name>", "reason": "<short>"}. '
            "If none of the routes clearly apply, use an empty route string."
        )
    return [
        Message(
            role="system",
            content=[TextContent(text=_CLASSIFIER_SYSTEM_PREFIX)],
        ),
        Message(role="user", content=[TextContent(text=prompt)]),
    ]


def _active_model_name(conversation: LocalConversation) -> str | None:
    from openhands.sdk.llm.router.base import RouterLLM

    llm = conversation.agent.llm
    if isinstance(llm, RouterLLM) and llm.active_llm is not None:
        return llm.active_llm.model
    return llm.model


def _classifier_reply_parts(text: str) -> tuple[str, str]:
    """Return normalized reply text and an optional JSON model/route candidate."""
    raw = (text or "").strip()
    raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()

    candidate = ""
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            obj = json.loads(match.group(0))
            candidate = str(obj.get("model") or obj.get("route") or "").strip()
    except Exception:  # noqa: BLE001
        candidate = ""
    return raw, candidate


def parse_direct_model(text: str, available_models: Sequence[str]) -> str | None:
    """Parse a direct-routing classifier reply into a target name.

    The expected contract is JSON with a ``model`` or ``route`` field, but the
    parser also scans the raw response for an allowed target name to tolerate
    fenced JSON or small formatting mistakes. Matching is case-insensitive, but
    the returned value is the canonical name from ``available_models``.
    """
    raw, candidate = _classifier_reply_parts(text)
    profile_by_casefold = {model.casefold(): model for model in available_models}

    if candidate:
        return profile_by_casefold.get(candidate.casefold())

    scan = raw.casefold()
    for model in sorted(available_models, key=len, reverse=True):
        if model.casefold() in scan:
            return model
    return None


def parse_router_route(text: str, router: RouterLLM) -> str | None:
    """Parse a classifier reply into a RouterLLM route name.

    Route names are authoritative, but classifiers often return the underlying
    provider model (for example ``openhands/gpt-5.5``) or a display label. Accept
    those aliases only when they map to exactly one route so ambiguous duplicate
    model IDs still fail visibly instead of silently choosing the wrong route.
    """
    target = parse_direct_model(text, router.available_routes())
    if target is not None:
        return target

    raw, candidate = _classifier_reply_parts(text)
    alias_to_routes: dict[str, set[str]] = {}
    alias_display: dict[str, str] = {}
    for route in router.route_infos():
        for alias in (route.label, route.model):
            alias = (alias or "").strip()
            if not alias or alias == route.name:
                continue
            key = alias.casefold()
            alias_to_routes.setdefault(key, set()).add(route.name)
            alias_display[key] = alias

    def unique_route_for_alias(alias: str) -> str | None:
        routes = alias_to_routes.get(alias.casefold())
        if routes is None or len(routes) != 1:
            return None
        return next(iter(routes))

    if candidate:
        target = unique_route_for_alias(candidate)
        if target is not None:
            return target

    scan = raw.casefold()
    for key in sorted(alias_display, key=lambda k: len(alias_display[k]), reverse=True):
        if key in scan:
            target = unique_route_for_alias(alias_display[key])
            if target is not None:
                return target
    return None


def _saved_profile_names(conversation: LocalConversation) -> list[str]:
    """Return saved LLM profile names without ``.json`` suffixes."""
    return [name.removesuffix(".json") for name in conversation._profile_store.list()]


def _recent_messages_text(
    conversation: LocalConversation, limit: int = _RECENT_MESSAGE_LIMIT
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
        meta_profile: MetaProfile | None = None,
        meta_profile_llms: dict[str, LLM] | None = None,
    ) -> None:
        # Resolve the meta-profile lazily (at invocation), not at construction,
        # so a missing/renamed file under ~/.openhands/meta-profiles produces a
        # tool error instead of breaking conversation startup.
        self._store = meta_profile_store
        self._active_meta_profile = active_meta_profile
        self._meta_profile = meta_profile
        self._meta_profile_llms = meta_profile_llms or {}

    def _resolve_meta_profile(self) -> MetaProfile:
        """Resolve the active meta-profile.

        When an ``active_meta_profile`` name is set, the store is authoritative
        (loaded by name) so the inline ``meta_profile`` blob and the store
        cannot diverge after a name change (``PATCH /api/settings``) or a
        file overwrite (``POST /api/meta-profiles/{name}``). The inline blob is
        only consulted when the store cannot resolve the name — e.g. cloud
        runtimes whose ephemeral filesystem has no meta-profile store. When no
        name is set, fall back to the inline blob if present, otherwise the
        first available meta-profile in the store.

        Raises:
            FileNotFoundError: If no meta-profile can be resolved.
            ValueError: If the resolved meta-profile is invalid.
        """
        name = self._active_meta_profile
        if name:
            try:
                return self._store.load(name)
            except FileNotFoundError:
                # Store cannot resolve the name (e.g. a cloud runtime with no
                # meta-profile store on disk); fall through to the inline blob.
                pass

        if self._meta_profile is not None:
            return self._meta_profile

        if name:
            available = self._store.list()
            raise FileNotFoundError(
                f"Active meta-profile '{name}' could not be resolved from the "
                "store or inline configuration. "
                f"Available meta-profiles: {', '.join(available) or 'none'}"
            )

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
        conversation: LocalConversation | None = None,
    ) -> ClassifyAndSwitchLLMObservation:
        from openhands.sdk.llm import content_to_str

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
                loaded = self._meta_profile_llms.get(meta.classifier_model)
                if loaded is None:
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
            classifier_llm = create_subscription_llm_from_config(
                loaded.model_copy(update={"usage_id": usage_id})
            )
            conversation.llm_registry.add(classifier_llm)

        # 2) Single classifier call over the recent conversation. If the active
        #    LLM is a RouterLLM, classify across its route names instead of saved
        #    profile names so the router remains the conversation LLM.
        transcript = _recent_messages_text(conversation) or "(no messages yet)"
        from openhands.sdk.llm.router.base import RouterLLM

        router_llm = conversation.agent.llm
        use_router_routes = isinstance(router_llm, RouterLLM)
        messages = (
            build_router_classifier_messages(meta, transcript, router_llm)
            if use_router_routes
            else build_classifier_messages(meta, transcript)
        )
        try:
            response = classifier_llm.completion(
                messages,
                call_context=conversation.get_llm_call_context(),
            )
        except Exception as exc:
            return ClassifyAndSwitchLLMObservation.from_text(
                text=f"Classifier call failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )

        reply = "\n".join(content_to_str(response.message.content))

        # 3a) RouterLLM mode: resolve and pin an existing router route.
        if use_router_routes:
            target_route = parse_router_route(reply, router_llm)
            if target_route is None:
                return ClassifyAndSwitchLLMObservation.from_text(
                    text=(
                        "Classifier reply did not resolve to an available RouterLLM "
                        f"route (reply: {reply!r}); refusing to silently route to "
                        "a default. Retry the tool or update the router route table."
                    ),
                    is_error=True,
                )
            chosen_class = f"route: {target_route}"
            try:
                conversation.switch_routed_llm(target_route)
            except Exception as exc:
                return ClassifyAndSwitchLLMObservation.from_text(
                    text=(
                        f"Failed to switch to RouterLLM route '{target_route}': "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    is_error=True,
                    model=target_route,
                    chosen_class=chosen_class,
                )

            active_model = _active_model_name(conversation)
            return ClassifyAndSwitchLLMObservation.from_text(
                text=(
                    f"Classified task as '{chosen_class}' and switched to "
                    f"RouterLLM route '{target_route}' (model '{active_model}'). "
                    "Future agent steps will use this route."
                ),
                chosen_class=chosen_class,
                model=target_route,
                active_model=active_model,
            )

        # 3b) Profile mode: resolve the target profile (chosen class or direct
        #     model). No silent fallback: when the classifier produces no usable
        #     answer the tool returns an error observation so the miss is visible.
        if meta.prompt_template is None:
            index = parse_class_index(reply, len(meta.classes))
            if index == 0:
                return ClassifyAndSwitchLLMObservation.from_text(
                    text=(
                        "Classifier did not pick a valid category "
                        f"(reply: {reply!r}); refusing to silently route to a "
                        "default. Retry the tool or fix the meta-profile classes."
                    ),
                    is_error=True,
                )
            chosen = meta.classes[index - 1]
            target_profile = chosen.model
            chosen_class = chosen.description
        else:
            target_profile = parse_direct_model(
                reply,
                sorted(
                    set(_saved_profile_names(conversation))
                    | set(self._meta_profile_llms)
                ),
            )
            if target_profile is None:
                return ClassifyAndSwitchLLMObservation.from_text(
                    text=(
                        "Classifier reply did not resolve to a saved LLM profile "
                        f"(reply: {reply!r}); refusing to silently route to a "
                        "default. Retry the tool or extend the model table."
                    ),
                    is_error=True,
                )
            chosen_class = f"model: {target_profile}"

        # 4) Switch the conversation to the target profile.
        try:
            inline_target = self._meta_profile_llms.get(target_profile)
            if inline_target is None:
                conversation.switch_profile(target_profile)
            else:
                conversation.switch_llm(
                    inline_target.model_copy(
                        update={"usage_id": f"profile:{target_profile}"}
                    )
                )
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

        active_model = _active_model_name(conversation)
        label = chosen_class or target_profile
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
    "suitable saved LLM profile or RouterLLM route.\n\n"
    "Use this near the start of a task (or when the task changes) to route "
    "the work to the best model. A classifier model inspects the recent "
    "conversation and, when the active conversation LLM is a RouterLLM, picks "
    "one of that router's route names. Otherwise it picks a target profile from "
    "the active meta-profile. The switch takes effect on the next LLM call."
)


class ClassifyAndSwitchLLMTool(
    ToolDefinition[ClassifyAndSwitchLLMAction, ClassifyAndSwitchLLMObservation]
):
    """Tool that classifies the task and switches to a meta-profile's LLM."""

    name = "route_task_to_model"
    user_selectable: ClassVar[bool] = False

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        active_meta_profile: str | None = None,
        meta_profile: MetaProfile | Mapping[str, object] | None = None,
        meta_profile_llms: Mapping[str, LLM | Mapping[str, object]] | None = None,
        meta_profile_store: MetaProfileStore | None = None,
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError(
                "ClassifyAndSwitchLLMTool only accepts 'active_meta_profile', "
                "'meta_profile', 'meta_profile_llms', and 'meta_profile_store'."
            )

        # Meta-profile resolution is deferred to invocation time (see
        # ClassifyAndSwitchLLMExecutor) so user-managed files under
        # ~/.openhands/meta-profiles cannot break conversation startup; a
        # missing/invalid profile surfaces as a tool error instead.
        store = meta_profile_store or MetaProfileStore()
        inline_meta_profile = (
            MetaProfile.model_validate(meta_profile)
            if meta_profile is not None
            else None
        )
        inline_llms = {
            name: LLM.model_validate(llm)
            for name, llm in (meta_profile_llms or {}).items()
        }
        return [
            cls(
                description=_DESCRIPTION,
                action_type=ClassifyAndSwitchLLMAction,
                observation_type=ClassifyAndSwitchLLMObservation,
                executor=ClassifyAndSwitchLLMExecutor(
                    store, active_meta_profile, inline_meta_profile, inline_llms
                ),
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
