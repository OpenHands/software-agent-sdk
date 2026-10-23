import re
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from functools import cache
from typing import Any, Literal, cast

from litellm import get_supported_openai_params
from litellm.utils import supports_vision as litellm_supports_vision

from openhands.sdk.llm.utils.openhands_provider import OPENHANDS_PROVIDER_PREFIX


def model_matches(model: str | None, patterns: Iterable[str]) -> bool:
    """Return True if any pattern appears as a substring in the raw model name.

    Matching semantics:
    - Case-insensitive substring search on full raw model string
    """
    raw = (model or "").strip().lower()
    for pat in patterns:
        token = pat.strip().lower()
        if token in raw:
            return True
    return False


@dataclass(frozen=True)
class ModelFeatures:
    supports_reasoning_effort: bool
    thinking_mode: Literal["adaptive", "manual", "none", "unknown"]
    supports_sampling_params: bool | None
    supports_extended_thinking: bool
    supports_prompt_cache: bool
    supports_prompt_cache_key: bool
    supports_stop_words: bool
    supports_responses_api: bool
    force_string_serializer: bool
    send_reasoning_content: bool
    supports_prompt_cache_retention: bool
    # True when the model's API rejects http(s) image URLs and only accepts
    # base64 ``data:`` URLs.
    requires_inline_image_data: bool
    # Effective capability from LiteLLM metadata plus SDK overrides.
    supports_vision: bool
    sources: Mapping[str, str] = field(default_factory=dict)


LITELLM_PROXY_PREFIX = "litellm_proxy/"

# Common deployment path prefixes used in LiteLLM proxy configurations
DEPLOYMENT_PREFIXES = ("prod/", "dev/", "staging/", "test/")


def _normalize_model_for_litellm(model: str | None) -> str | None:
    """Remove SDK/proxy routing prefixes before querying LiteLLM metadata."""
    if not model:
        return None

    normalized = model.strip().lower()
    for provider_prefix in (LITELLM_PROXY_PREFIX, OPENHANDS_PROVIDER_PREFIX):
        if normalized.startswith(provider_prefix):
            normalized = normalized.removeprefix(provider_prefix)
            break

    # Strip deployment prefixes (e.g., "prod/", "dev/", "staging/", "test/")
    for prefix in DEPLOYMENT_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break

    if normalized == "kimi-k3":
        return "moonshot/kimi-k3"

    return normalized


# Provider labels that describe the SDK/proxy routing layer rather than the
# upstream that actually serves the request. LiteLLM returns the full OpenAI
# param set for these (including ``prompt_cache_key`` for Anthropic models),
# so they must never be used as the ``custom_llm_provider`` hint for param
# resolution -- doing so re-introduces the 400 UnsupportedParamsError from #5325.
_PASSTHROUGH_PROVIDERS: frozenset[str] = frozenset({"litellm_proxy", "openhands"})


def _real_litellm_provider(
    model_info: Mapping[str, Any] | None,
) -> str | None:
    """Return the upstream provider that serves the request, if known.

    For ``openhands/`` and ``litellm_proxy/`` aliases the model is routed
    through a proxy, so the bare model name is unresolvable by LiteLLM and
    ``get_supported_openai_params`` returns ``None``. The proxy's model info
    carries the real upstream provider (e.g. ``deepseek``, ``anthropic``) in
    ``litellm_provider``; threading it into the lookup restores correct param
    detection. Passthrough labels (``litellm_proxy``/``openhands``) are
    discarded because LiteLLM over-reports support for them.
    """
    if model_info is None:
        return None
    provider = model_info.get("litellm_provider")
    if not isinstance(provider, str):
        return None
    provider = provider.strip().lower()
    if not provider or provider in _PASSTHROUGH_PROVIDERS:
        return None
    return provider


@cache
def _normalized_supported_openai_params(
    model: str | None, provider_hint: str | None = None
) -> frozenset[str]:
    """Return LiteLLM-supported OpenAI params for a normalized model name.

    ``provider_hint`` is the real upstream provider for proxied/aliased models
    (see ``_real_litellm_provider``). When the bare name is unresolvable --
    the case for ``openhands/`` and ``litellm_proxy/`` aliases -- the hint is
    used so param support reflects the provider that actually serves the
    request instead of silently defaulting to ``False`` (#5328 follow-up).

    NOTE: The provider-scoped lookup broadens the whole param set (e.g. for
    DeepSeek it also reports ``reasoning_effort``). Callers that only want to
    recover ``prompt_cache_key`` must not feed the hinted set into unrelated
    capabilities -- use ``_supports_prompt_cache_key_param`` instead of reading
    this set directly. See ``get_features``.
    """
    normalized = _normalize_model_for_litellm(model)
    if not normalized:
        return frozenset()

    params = get_supported_openai_params(
        model=normalized,
        custom_llm_provider=None,
    )
    if not params and provider_hint:
        params = get_supported_openai_params(
            model=normalized,
            custom_llm_provider=provider_hint,
        )
    return frozenset(params or ())


def _supports_prompt_cache_key_param(
    model: str | None, provider_hint: str | None
) -> bool:
    """Whether the serving provider accepts the ``prompt_cache_key`` param.

    Resolves from the bare-name param set first; only when that is
    unresolvable (proxied/aliased models) does it consult the provider-scoped
    set. Scoping the hint to this single capability keeps the broader
    provider-scoped param set from leaking into unrelated features such as
    ``supports_reasoning_effort`` (#5332 review follow-up).
    """
    if "prompt_cache_key" in _normalized_supported_openai_params(model):
        return True
    if provider_hint is None:
        return False
    return "prompt_cache_key" in _normalized_supported_openai_params(
        model, provider_hint
    )


# Reviewed behavioral defaults, not a catalog of every capability of each model.
# Rules are ordered; route-specific restrictions override model-family defaults.
_MODEL_CAPABILITIES: tuple[tuple[str, dict[str, bool | str]], ...] = (
    ("claude-3-7-sonnet*", {"supports_prompt_cache": True}),
    ("claude-sonnet-3-7-latest", {"supports_prompt_cache": True}),
    ("claude-3-5-sonnet*", {"supports_prompt_cache": True}),
    ("claude-3-5-haiku*", {"supports_prompt_cache": True}),
    ("claude-3-haiku-20240307*", {"supports_prompt_cache": True}),
    ("claude-3-opus-20240229*", {"supports_prompt_cache": True}),
    ("claude-sonnet-4*", {"supports_prompt_cache": True}),
    ("claude-opus-4*", {"supports_prompt_cache": True}),
    ("claude-sonnet-4-5*", {"thinking_mode": "manual"}),
    ("claude-sonnet-4-6*", {"thinking_mode": "manual"}),
    ("claude-haiku-4-5*", {"thinking_mode": "manual", "supports_prompt_cache": True}),
    ("claude-haiku-5-5*", {"supports_prompt_cache": True}),
    ("claude-opus-5*", {"supports_prompt_cache": True}),
    ("claude-sonnet-5*", {"supports_prompt_cache": True}),
    ("claude-fable-5*", {"supports_prompt_cache": True}),
    (
        "gpt-5*",
        {"supports_responses_api": True, "supports_prompt_cache_retention": True},
    ),
    ("gpt-6*", {"supports_responses_api": True}),
    ("gpt-4.1*", {"supports_prompt_cache_retention": True}),
    ("*mini*", {"supports_prompt_cache_retention": False}),
    ("gpt-5.1-codex-mini*", {"supports_prompt_cache_retention": True}),
    ("azure/*", {"supports_prompt_cache_retention": False}),
    ("codex-mini-latest", {"supports_responses_api": True}),
    ("o1*", {"supports_stop_words": False}),
    ("o3*", {"supports_stop_words": False}),
    ("grok-4-0709", {"supports_stop_words": False}),
    ("grok-code-fast-1", {"supports_stop_words": False}),
    ("deepseek-r1-0528*", {"supports_stop_words": False}),
    ("bedrock/*openai.gpt-*", {"supports_stop_words": False}),
    ("deepseek-v3.2*", {"force_string_serializer": True}),
    ("glm*", {"force_string_serializer": True}),
    ("groq/kimi-k2-instruct*", {"force_string_serializer": True}),
    ("openrouter/minimax/*", {"force_string_serializer": True}),
    (
        "openrouter/minimax-m2*",
        {"force_string_serializer": True, "send_reasoning_content": True},
    ),
    ("kimi-k2-thinking*", {"send_reasoning_content": True}),
    ("kimi-k2.5", {"send_reasoning_content": True}),
    ("kimi-k2.6", {"send_reasoning_content": True}),
    (
        "kimi-k3",
        {
            "supports_vision": True,
            "supports_reasoning_effort": True,
            "send_reasoning_content": True,
        },
    ),
    ("deepseek-reasoner", {"send_reasoning_content": True}),
    ("deepseek-v4-pro", {"send_reasoning_content": True}),
    ("deepseek-v4-flash", {"send_reasoning_content": True}),
    # Verified user and tool images through the OpenHands proxy (#5460, #5523).
    ("deepseek-v4.1-flash", {"supports_vision": True, "send_reasoning_content": True}),
    # URL restrictions are properties of the serving route, not the model family.
    ("moonshot/kimi-k2.6", {"requires_inline_image_data": True}),
    ("moonshot/kimi-k3", {"requires_inline_image_data": True}),
    ("openhands/kimi-k3", {"requires_inline_image_data": True}),
)


def _owned_capabilities(model: str | None) -> dict[str, bool | str]:
    route = (_normalize_model_for_litellm(model) or "").lower()
    # LiteLLM's custom provider uses opaque deployment IDs, not model IDs.
    identity = route if route.startswith("custom/") else route.rsplit("/", 1)[-1]
    identity = re.sub(
        r"^(?:(?:us|eu|apac|global)\.)?"
        r"(?:anthropic|openai|moonshotai)[.-]",
        "",
        identity,
    )
    values: dict[str, bool | str] = {}
    for pattern, capabilities in _MODEL_CAPABILITIES:
        candidate = route if "/" in pattern else identity
        if fnmatchcase(candidate, pattern) or (
            "/" in pattern and fnmatchcase((model or "").lower(), pattern)
        ):
            values.update(capabilities)
    return values


@cache
def _model_supports_vision(model: str | None) -> bool:
    normalized = _normalize_model_for_litellm(model)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return bool(normalized and litellm_supports_vision(normalized))


def _optional_bool(source: Mapping[str, Any] | None, key: str) -> bool | None:
    if source is None:
        return None
    value = source.get(key)
    return value if isinstance(value, bool) else None


def get_features(
    model: str | None,
    model_info: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> ModelFeatures:
    """Resolve caller > endpoint > SDK defaults > discovered capabilities.

    LiteLLM metadata is not an endpoint declaration: stale registry entries must
    not override SDK-owned facts. Explicit endpoint restrictions still win.
    """
    metadata = dict(model_info or {})
    owned = _owned_capabilities(model)
    sources: dict[str, str] = {}
    endpoint = metadata.get("_capability_source") != "litellm"
    supported_params = _normalized_supported_openai_params(model)

    def resolve(key: str, fallback: bool, metadata_key: str | None = None) -> bool:
        entries = [
            ("caller", _optional_bool(overrides, key)),
            (
                "endpoint" if endpoint else "litellm",
                _optional_bool(metadata, metadata_key or key),
            ),
            ("sdk", _optional_bool(owned, key)),
        ]
        if not endpoint:
            entries[1], entries[2] = entries[2], entries[1]
        for source, value in entries:
            if value is not None:
                sources[key] = source
                return value
        sources[key] = "discovery"
        return fallback

    declared_params = metadata.get("supported_openai_params")
    allowed_params = metadata.get("allowed_openai_params")
    if _optional_bool(metadata, "supports_reasoning_effort") is None:
        if isinstance(declared_params, (list, tuple)):
            metadata["supports_reasoning_effort"] = (
                "reasoning_effort" in declared_params
            )
        if (
            isinstance(allowed_params, (list, tuple))
            and "reasoning_effort" in allowed_params
        ):
            metadata["supports_reasoning_effort"] = True
    reasoning = "reasoning_effort" in supported_params
    if metadata.get("supports_reasoning") is False:
        reasoning = False
    # Generic supports_reasoning=True does not authorize reasoning_effort.
    reasoning = resolve("supports_reasoning_effort", reasoning)
    if metadata.get("supports_adaptive_thinking") is True:
        metadata["thinking_mode"] = "adaptive"
    thinking = "unknown" if metadata.get("supports_reasoning") is True else "none"
    thinking_values = [
        ("caller", (overrides or {}).get("thinking_mode")),
        ("endpoint" if endpoint else "litellm", metadata.get("thinking_mode")),
        ("sdk", owned.get("thinking_mode")),
    ]
    if not endpoint:
        thinking_values[1], thinking_values[2] = thinking_values[2], thinking_values[1]
    for source, candidate in thinking_values:
        if candidate in {"adaptive", "manual", "none", "unknown"}:
            thinking = str(candidate)
            sources["thinking_mode"] = source
            break
    sources.setdefault("thinking_mode", "discovery")
    if (
        endpoint
        and metadata.get("supports_reasoning") is False
        and not (overrides or {}).get("thinking_mode")
    ):
        thinking = "none"
    explicit_cache = metadata.get("supports_prompt_caching")
    cache = bool(explicit_cache) and (
        _real_litellm_provider(metadata) == "anthropic"
        or "claude" in (model or "").lower()
        or "anthropic" in str(metadata.get("key", "")).lower()
    )
    cache_key = (
        "supports_prompt_caching"
        if explicit_cache is False
        else "supports_prompt_cache"
    )
    endpoints = metadata.get("supported_endpoints")
    responses = (
        "/v1/responses" in endpoints if isinstance(endpoints, (list, tuple)) else False
    )
    if isinstance(endpoints, (list, tuple)) and endpoint:
        metadata["supports_responses_api"] = responses
    sampling = _optional_bool(overrides, "supports_sampling_params")
    if sampling is None:
        sampling = _optional_bool(metadata, "supports_sampling_params")
    return ModelFeatures(
        supports_reasoning_effort=reasoning,
        thinking_mode=cast(Literal["adaptive", "manual", "none", "unknown"], thinking),
        supports_sampling_params=sampling,
        supports_extended_thinking=thinking == "manual",
        supports_prompt_cache=resolve("supports_prompt_cache", cache, cache_key),
        supports_prompt_cache_key=resolve(
            "supports_prompt_cache_key",
            _supports_prompt_cache_key_param(model, _real_litellm_provider(metadata)),
        ),
        supports_stop_words=resolve("supports_stop_words", True),
        supports_responses_api=resolve("supports_responses_api", responses),
        force_string_serializer=resolve("force_string_serializer", False),
        send_reasoning_content=resolve("send_reasoning_content", False),
        supports_prompt_cache_retention=resolve(
            "supports_prompt_cache_retention", False
        ),
        requires_inline_image_data=resolve("requires_inline_image_data", False),
        supports_vision=resolve("supports_vision", _model_supports_vision(model)),
        sources=sources,
    )
