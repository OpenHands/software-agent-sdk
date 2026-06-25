from __future__ import annotations

import copy
import importlib
import json
import os
import threading
import warnings
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, ClassVar, Literal, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from openhands.sdk.llm.fallback_strategy import FallbackStrategy
from openhands.sdk.llm.utils.model_info import get_litellm_model_info
from openhands.sdk.settings.metadata import SettingProminence, field_meta
from openhands.sdk.utils.pydantic_secrets import serialize_secret, validate_secret


if TYPE_CHECKING:  # type hints only, avoid runtime import cycle
    from openhands.sdk.llm.auth import SupportedVendor
    from openhands.sdk.llm.auth.openai import OpenAIAuthMethod
    from openhands.sdk.tool.tool import ToolDefinition

from openhands.sdk.llm.auth.openai import transform_for_subscription


with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import litellm

from typing import Final, cast

from litellm import (
    ChatCompletionToolParam,
    CustomStreamWrapper,
    ResponseInputParam,
    acompletion as litellm_acompletion,
    completion as litellm_completion,
)
from litellm.exceptions import (
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout as LiteLLMTimeout,
)
from litellm.responses.main import (
    aresponses as litellm_aresponses,
    responses as litellm_responses,
)
from litellm.responses.streaming_iterator import (
    ResponsesAPIStreamingIterator,
    SyncResponsesAPIStreamingIterator,
)
from litellm.types.llms.openai import (
    OutputTextDeltaEvent,
    ReasoningSummaryTextDeltaEvent,
    RefusalDeltaEvent,
    ResponseCompletedEvent,
    ResponsesAPIResponse,
    ResponsesAPIStreamEvents,
)
from litellm.types.utils import (
    Delta,
    ModelResponse,
    ModelResponseStream,
    StreamingChoices,
)
from litellm.utils import (
    create_pretrained_tokenizer,
    supports_vision,
    token_counter,
)

from openhands.sdk.llm.exceptions import (
    LLMContextWindowTooSmallError,
    LLMNoResponseError,
    is_prompt_cache_too_small,
    map_provider_exception,
)

# OpenHands utilities
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.message import (
    Message,
)
from openhands.sdk.llm.mixins.non_native_fc import NonNativeToolCallingMixin
from openhands.sdk.llm.options.chat_options import select_chat_options
from openhands.sdk.llm.options.responses_options import select_responses_options
from openhands.sdk.llm.streaming import (
    AnyTokenCallbackType,
    TokenCallbackType,
    _invoke_token_callback,
)
from openhands.sdk.llm.utils.image_inline import (
    amaybe_inline_image_urls,
    maybe_inline_image_urls,
)
from openhands.sdk.llm.utils.image_resize import maybe_resize_messages_for_provider
from openhands.sdk.llm.utils.litellm_provider import infer_litellm_provider
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.llm.utils.model_features import get_features
from openhands.sdk.llm.utils.openhands_provider import (
    LiteLLMCallKwargs,
    canonicalize_openhands_llm_payload,
    litellm_call_kwargs,
)
from openhands.sdk.llm.utils.retry_mixin import RetryMixin
from openhands.sdk.llm.utils.telemetry import Telemetry
from openhands.sdk.llm.utils.vertex_preflight import assert_vertex_sdk_available
from openhands.sdk.logger import ENV_LOG_DIR, get_logger


logger = get_logger(__name__)

__all__ = ["LLM"]


# Exceptions we retry on
LLM_RETRY_EXCEPTIONS: Final[tuple[type[Exception], ...]] = (
    APIConnectionError,
    RateLimitError,
    ServiceUnavailableError,
    LiteLLMTimeout,
    InternalServerError,
    LLMNoResponseError,
)

# Minimum context window size required for OpenHands to function properly.
# Based on typical usage: system prompt (~2k) + conversation history (~4k)
# + tool definitions (~2k) + working memory (~8k) = ~16k minimum.
MIN_CONTEXT_WINDOW_TOKENS: Final[int] = 16384

# Environment variable to override the minimum context window check
ENV_ALLOW_SHORT_CONTEXT_WINDOWS: Final[str] = "ALLOW_SHORT_CONTEXT_WINDOWS"

# Default max output tokens when model info only provides 'max_tokens' (ambiguous).
# Some providers use 'max_tokens' for the total context window, not output limit.
# This cap prevents requesting output that exceeds the context window.
# 16384 is a safe default that works for most models (GPT-4o: 16k, Claude: 8k).
DEFAULT_MAX_OUTPUT_TOKENS_CAP: Final[int] = 16384

# Some providers (notably AWS Bedrock for Anthropic models) enforce
# ``input_tokens + max_tokens <= context_window`` on a single shared window.
# For these providers we clamp the per-request output budget at request time so
# our injected default ``max_tokens`` (which defaults to the model's full
# ``max_output_tokens`` -- e.g. 64k for Sonnet 4.5) cannot push large-input
# requests past the context window. See BerriAI/litellm#17900 for the upstream
# default change that made this manifest.
JOINT_BUDGET_SAFETY_MARGIN_TOKENS: Final[int] = 256
JOINT_BUDGET_MIN_OUTPUT_TOKENS: Final[int] = 1024
# Provider name prefixes known to enforce a joint input/output token budget.
# Kept as a narrow allowlist so direct providers (Anthropic, OpenAI, etc.) --
# which have independent input/output windows -- are not affected. We match by
# prefix because LiteLLM uses both ``bedrock`` (raw provider inference) and
# ``bedrock_converse`` (the Anthropic-on-Bedrock route, surfaced via the model
# registry) for the same underlying API.
_JOINT_BUDGET_PROVIDER_PREFIXES: Final[tuple[str, ...]] = ("bedrock",)

# Secret-bearing fields on LLM. Kept as a single source of truth so callers that
# need to walk secrets (e.g. cipher-aware decryption on the save path) stay in
# sync with the serializer below.
LLM_SECRET_FIELDS: Final[tuple[str, ...]] = (
    "api_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
)

LLM_PROFILE_SCHEMA_VERSION: Final[int] = 1


class LLM(BaseModel, RetryMixin, NonNativeToolCallingMixin):
    """Language model interface for OpenHands agents.

    The LLM class provides a unified interface for interacting with various
    language models through the litellm library. It handles model configuration,
    API authentication, retry logic, and tool calling capabilities.

    Attributes:
        model: Model name (e.g., "gpt-5.5").
        api_key: API key for authentication.
        base_url: Custom API base URL.
        num_retries: Number of retry attempts for failed requests.
        timeout: Request timeout in seconds.

    Example:
        ```python
        from openhands.sdk import LLM
        from pydantic import SecretStr

        llm = LLM(
            model="gpt-5.5",
            api_key=SecretStr("your-api-key"),
            usage_id="my-agent"
        )
        # Use with agent or conversation
        ```
    """

    # =========================================================================
    # Config fields
    # =========================================================================

    model: str = Field(
        default="gpt-5.5",
        description="Model name.",
        json_schema_extra=field_meta(SettingProminence.CRITICAL),
    )
    api_key: str | SecretStr | None = Field(
        default=None,
        description="API key.",
        json_schema_extra=field_meta(
            SettingProminence.CRITICAL,
            label="API Key",
        ),
    )
    auth_type: Literal["api_key", "subscription"] = Field(
        default="api_key",
        description="Authentication mode for the LLM.",
        json_schema_extra=field_meta(
            SettingProminence.CRITICAL,
            label="Authentication",
        ),
    )
    subscription_vendor: Literal["openai"] | None = Field(
        default=None,
        description="Subscription provider for subscription-backed LLM access.",
        json_schema_extra=field_meta(
            SettingProminence.CRITICAL,
            label="Subscription provider",
            depends_on=("auth_type",),
        ),
    )
    base_url: str | None = Field(
        default=None,
        description="Custom base URL.",
        json_schema_extra=field_meta(SettingProminence.MAJOR),
    )
    api_version: str | None = Field(
        default=None,
        description="API version (e.g., Azure).",
    )

    aws_access_key_id: str | SecretStr | None = Field(
        default=None,
    )
    aws_secret_access_key: str | SecretStr | None = Field(
        default=None,
    )
    aws_session_token: str | SecretStr | None = Field(
        default=None,
    )
    aws_region_name: str | None = Field(
        default=None,
    )
    aws_profile_name: str | None = Field(
        default=None,
    )
    aws_role_name: str | None = Field(
        default=None,
    )
    aws_session_name: str | None = Field(
        default=None,
    )
    aws_bedrock_runtime_endpoint: str | None = Field(
        default=None,
    )

    # OpenRouter uses HTTP-Referer as the app identity for rankings.
    # Keep this stable unless the OpenRouter app attribution is migrated.
    openrouter_site_url: str = Field(
        default="https://docs.all-hands.dev/",
    )
    openrouter_app_name: str = Field(
        default="OpenHands",
    )

    num_retries: int = Field(default=5, ge=0)
    retry_multiplier: float = Field(default=8.0, ge=0)
    retry_min_wait: int = Field(default=8, ge=0)
    retry_max_wait: int = Field(default=64, ge=0)

    timeout: int | None = Field(
        default=300,
        ge=0,
        description="HTTP timeout in seconds. Default is 300s (5 minutes). "
        "Set to None to disable timeout (not recommended for production).",
    )

    max_message_chars: int = Field(
        default=30_000,
        ge=1,
        description="Approx max chars in each event/content sent to the LLM.",
    )

    temperature: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Sampling temperature for response generation. "
            "Defaults to None (uses provider default temperature). "
            "Set to 0.0 for deterministic outputs, "
            "or higher values (0.7-1.0) for more creative responses."
        ),
    )
    top_p: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "Nucleus sampling parameter. "
            "Defaults to None (uses provider default). "
            "Set to a value between 0 and 1 to control diversity of outputs."
        ),
    )
    top_k: float | None = Field(default=None, ge=0)

    max_input_tokens: int | None = Field(
        default=None,
        ge=1,
        description="The maximum number of input tokens. "
        "Note that this is currently unused, and the value at runtime is actually"
        " the total tokens in OpenAI (e.g. 128,000 tokens for GPT-4).",
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description="The maximum number of output tokens. This is sent to the LLM.",
    )
    model_canonical_name: str | None = Field(
        default=None,
        description=(
            "Optional canonical model name for feature registry lookups. "
            "The OpenHands SDK maintains a model feature registry that "
            "maps model names to capabilities (e.g., vision support, "
            "prompt caching, responses API support). When using proxied or "
            "aliased model identifiers, set this field to the canonical "
            "model name (e.g., 'openai/gpt-4o') to ensure correct "
            "capability detection. If not provided, the 'model' field "
            "will be used for capability lookups."
        ),
    )
    extra_headers: dict[str, str] | None = Field(
        default=None,
        description="Optional HTTP headers to forward to LiteLLM requests.",
    )
    input_cost_per_token: float | None = Field(
        default=None,
        ge=0,
        description="The cost per input token. This will available in logs for user.",
    )
    output_cost_per_token: float | None = Field(
        default=None,
        ge=0,
        description="The cost per output token. This will available in logs for user.",
    )
    ollama_base_url: str | None = Field(
        default=None,
    )

    stream: bool = Field(
        default=False,
        description=(
            "Enable streaming responses from the LLM. "
            "When enabled, the provided `on_token` callback in .completions "
            "and .responses will be invoked for each chunk of tokens."
        ),
    )
    drop_params: bool = Field(default=True)
    modify_params: bool = Field(
        default=True,
        description="Modify params allows litellm to do transformations like adding"
        " a default message, when a message is empty.",
    )
    disable_vision: bool | None = Field(
        default=None,
        description="If model is vision capable, this option allows to disable image "
        "processing (useful for cost reduction).",
    )
    disable_stop_word: bool | None = Field(
        default=False,
        description="Disable using of stop word.",
    )
    caching_prompt: bool = Field(
        default=True,
        description="Enable caching of prompts.",
    )
    log_completions: bool = Field(
        default=False,
        description="Enable logging of completions.",
    )
    log_completions_folder: str = Field(
        default=os.path.join(ENV_LOG_DIR, "completions"),
        description="The folder to log LLM completions to. "
        "Required if log_completions is True.",
    )
    custom_tokenizer: str | None = Field(
        default=None,
        description="A custom tokenizer to use for token counting.",
    )
    native_tool_calling: bool = Field(
        default=True,
        description="Whether to use native tool calling.",
    )
    force_string_serializer: bool | None = Field(
        default=None,
        description=(
            "Force using string content serializer when sending to LLM API. "
            "If None (default), auto-detect based on model. "
            "Useful for providers that do not support list content, "
            "like HuggingFace and Groq."
        ),
    )
    inline_image_urls: bool | None = Field(
        default=None,
        description=(
            "If True, fetch any http(s) image URL in outgoing messages and "
            "inline it as a base64 ``data:`` URL before sending. If None "
            "(default), auto-detect based on model (some APIs such as "
            "Moonshot's public Kimi endpoint reject URL-formatted images "
            "and require base64). Set this explicitly when the model is "
            "reached through a proxy alias that hides the underlying "
            "provider (e.g. ``litellm_proxy/<custom-alias>``). Note: "
            "inlining only runs when ``vision_is_active()`` is True, so "
            "the alias must still be recognised as vision-capable by "
            "litellm — otherwise images are not sent at all and there is "
            "nothing to inline."
        ),
    )
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "none"] | None = Field(
        default="high",
        description="The effort to put into reasoning. "
        "This is a string that can be one of 'low', 'medium', 'high', 'xhigh', "
        "or 'none'. "
        "Can apply to all reasoning models.",
    )
    reasoning_summary: Literal["auto", "concise", "detailed"] | None = Field(
        default=None,
        description="The level of detail for reasoning summaries. "
        "This is a string that can be one of 'auto', 'concise', or 'detailed'. "
        "Requires verified OpenAI organization. Only sent when explicitly set.",
    )
    enable_encrypted_reasoning: bool = Field(
        default=True,
        description="If True, ask for ['reasoning.encrypted_content'] "
        "in Responses API include.",
    )
    # Prompt cache retention is filtered per model features in chat options.
    prompt_cache_retention: str | None = Field(
        default="24h",
        description=(
            "Retention policy for prompt cache. Only sent for supported models "
            "(GPT-5+ and GPT-4.1, excluding Azure deployments); explicitly "
            "stripped for all others."
        ),
    )
    extended_thinking_budget: int | None = Field(
        default=200_000,
        description="The budget tokens for extended thinking, "
        "supported by Anthropic models.",
    )
    seed: int | None = Field(
        default=None,
        description="The seed to use for random number generation.",
    )
    usage_id: str = Field(
        default="default",
        serialization_alias="usage_id",
        description=(
            "Unique usage identifier for the LLM. Used for registry lookups, "
            "telemetry, and spend tracking."
        ),
    )
    litellm_extra_body: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Additional key-value pairs to pass to litellm's extra_body parameter. "
            "This is useful for custom inference endpoints that need additional "
            "parameters for configuration, routing, or advanced features. "
            "NOTE: Not all LLM providers support extra_body parameters. Some providers "
            "(e.g., OpenAI) may reject requests with unrecognized options. "
            "This is commonly supported by: "
            "- LiteLLM proxy servers (routing metadata, tracing) "
            "- vLLM endpoints (return_token_ids, etc.) "
            "- Custom inference clusters "
            "Examples: "
            "- Proxy routing: {'trace_version': '1.0.0', 'tags': ['agent:my-agent']} "
            "- vLLM features: {'return_token_ids': True}"
        ),
    )

    fallback_strategy: FallbackStrategy | None = Field(
        default=None,
        description=(
            "Optional fallback strategy for trying alternate LLMs on transient "
            "failure. Construct with FallbackStrategy(fallback_llms=[...])."
            "Excluded from serialization; must be reconfigured after load."
        ),
        exclude=True,
    )

    # =========================================================================
    # Internal fields (excluded from dumps)
    # =========================================================================
    retry_listener: SkipJsonSchema[
        Callable[[int, int, BaseException | None], None] | None
    ] = Field(
        default=None,
        exclude=True,
    )
    _metrics: Metrics | None = PrivateAttr(default=None)
    # Runtime-only private attrs
    _model_info: Any = PrivateAttr(default=None)
    _tokenizer: Any = PrivateAttr(default=None)
    _chat_template_tokenizer: Any = PrivateAttr(default=None)
    _telemetry: Telemetry | None = PrivateAttr(default=None)
    _is_subscription: bool = PrivateAttr(default=False)
    _subscription_credential_store: Any = PrivateAttr(default=None)
    _subscription_credentials: Any = PrivateAttr(default=None)
    _litellm_provider: str | None = PrivateAttr(default=None)
    _prompt_cache_key: str | None = PrivateAttr(default=None)
    _effective_max_input_tokens: int | None = PrivateAttr(default=None)
    _effective_max_output_tokens: int | None = PrivateAttr(default=None)
    _litellm_modify_params_lock: ClassVar[threading.RLock] = threading.RLock()

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", arbitrary_types_allowed=True
    )

    # =========================================================================
    # Validators
    # =========================================================================
    @field_validator(
        "api_key", "aws_access_key_id", "aws_secret_access_key", "aws_session_token"
    )
    @classmethod
    def _validate_secrets(cls, v: str | SecretStr | None, info) -> SecretStr | None:
        return validate_secret(v, info)

    @model_validator(mode="before")
    @classmethod
    def _coerce_inputs(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)

        model_val = d.get("model")
        if not model_val:
            raise ValueError("model must be specified in LLM")

        # Azure default version
        if model_val.startswith("azure") and not d.get("api_version"):
            d["api_version"] = "2024-12-01-preview"

        # Fix base_url for direct OpenAI - API expects /v1 suffix
        # If base_url is "https://api.openai.com", set to None to use LiteLLM default
        if model_val.startswith("openai/"):
            base = d.get("base_url")
            if base == "https://api.openai.com" or base == "https://api.openai.com/":
                d["base_url"] = None  # Let LiteLLM use its default which includes /v1

        return d

    @model_validator(mode="after")
    def _post_init(self):
        # NOTE: AWS credentials and OpenRouter site/app identifiers are NOT
        # written to ``os.environ`` here. Doing so in a multi-tenant agent
        # server would let one conversation's credentials bleed into another
        # via the shared process environment (see issue #3138). Instead,
        # AWS credentials flow per-call through ``_aws_kwargs()`` and the
        # OpenRouter ``HTTP-Referer`` / ``X-Title`` headers flow per-call
        # through ``_openrouter_headers()``.

        # Metrics + Telemetry wiring. Guard both: this validator re-runs whenever
        # the LLM is passed into another Pydantic model (e.g. RegistryEvent),
        # and replacing _telemetry would silently drop any callback callers
        # have attached via telemetry.set_*_callback().
        if self._metrics is None:
            self._metrics = Metrics(model_name=self.model)

        if self._telemetry is None:
            self._telemetry = Telemetry(
                model_name=self.model,
                log_enabled=self.log_completions,
                log_dir=self.log_completions_folder if self.log_completions else None,
                input_cost_per_token=self.input_cost_per_token,
                output_cost_per_token=self.output_cost_per_token,
                metrics=self._metrics,
            )

        # Tokenizer
        if self.custom_tokenizer:
            self._chat_template_tokenizer = self._load_chat_template_tokenizer(
                self.custom_tokenizer
            )
            if self._chat_template_tokenizer is None:
                try:
                    self._tokenizer = create_pretrained_tokenizer(self.custom_tokenizer)
                except Exception:
                    logger.debug(
                        "Unable to load LiteLLM tokenizer for %s",
                        self.custom_tokenizer,
                        exc_info=True,
                    )
                    self._tokenizer = None

        # Capabilities + model info
        self._init_model_info_and_caps()

        logger.debug(
            f"LLM ready: model={self.model} base_url={self.base_url} "
            f"reasoning_effort={self.reasoning_effort} "
            f"temperature={self.temperature}"
        )
        return self

    def _openrouter_headers(self) -> dict[str, str]:
        """Build OpenRouter HTTP-Referer / X-Title headers for per-call use.

        Returns an empty dict when neither field is set. Passed via
        ``extra_headers`` so litellm forwards them on the OpenRouter request
        without us having to mutate ``os.environ`` (which would leak across
        conversations in a multi-tenant server; see issue #3138).
        """
        headers: dict[str, str] = {}
        if self.openrouter_site_url:
            headers["HTTP-Referer"] = self.openrouter_site_url
        if self.openrouter_app_name:
            headers["X-Title"] = self.openrouter_app_name
        return headers

    def _aws_kwargs(self) -> dict[str, str]:
        """Build kwargs dict for AWS params to pass to litellm calls."""
        kw: dict[str, str] = {}
        if self.aws_access_key_id:
            assert isinstance(self.aws_access_key_id, SecretStr)
            kw["aws_access_key_id"] = self.aws_access_key_id.get_secret_value()
        if self.aws_secret_access_key:
            assert isinstance(self.aws_secret_access_key, SecretStr)
            kw["aws_secret_access_key"] = self.aws_secret_access_key.get_secret_value()
        if self.aws_session_token:
            assert isinstance(self.aws_session_token, SecretStr)
            kw["aws_session_token"] = self.aws_session_token.get_secret_value()
        if self.aws_region_name:
            kw["aws_region_name"] = self.aws_region_name
        if self.aws_profile_name:
            kw["aws_profile_name"] = self.aws_profile_name
        if self.aws_role_name:
            kw["aws_role_name"] = self.aws_role_name
        if self.aws_session_name:
            kw["aws_session_name"] = self.aws_session_name
        if self.aws_bedrock_runtime_endpoint:
            kw["aws_bedrock_runtime_endpoint"] = self.aws_bedrock_runtime_endpoint
        return kw

    def _retry_listener_fn(
        self, attempt_number: int, num_retries: int, _err: BaseException | None
    ) -> None:
        if self.retry_listener is not None:
            self.retry_listener(attempt_number, num_retries, _err)
        # NOTE: don't call Telemetry.on_error here.
        # This function runs for each retried failure (before the next attempt),
        # which would create noisy duplicate error logs.
        # The completion()/responses() exception handlers call Telemetry.on_error
        # after retries are exhausted (final failure), which is what we want to log.

    # =========================================================================
    # Serializers
    # =========================================================================
    @field_serializer(*LLM_SECRET_FIELDS, when_used="always")
    def _serialize_secrets(self, v: SecretStr | None, info):
        return serialize_secret(v, info)

    # =========================================================================
    # Public API
    # =========================================================================
    @property
    def metrics(self) -> Metrics:
        """Get usage metrics for this LLM instance.

        Returns:
            Metrics object containing token usage, costs, and other statistics.

        Example:
            ```python
            cost = llm.metrics.accumulated_cost
            print(f"Total cost: ${cost}")
            ```
        """
        if self._metrics is None:
            self._metrics = Metrics(model_name=self.model)
        return self._metrics

    @property
    def telemetry(self) -> Telemetry:
        """Get telemetry handler for this LLM instance.

        Returns:
            Telemetry object for managing logging and metrics callbacks.

        Example:
            ```python
            llm.telemetry.set_log_completions_callback(my_callback)
            ```
        """
        if self._telemetry is None:
            self._telemetry = Telemetry(
                model_name=self.model,
                log_enabled=self.log_completions,
                log_dir=self.log_completions_folder if self.log_completions else None,
                input_cost_per_token=self.input_cost_per_token,
                output_cost_per_token=self.output_cost_per_token,
                metrics=self.metrics,
            )
        return self._telemetry

    @computed_field(
        return_type=bool,
        description=(
            "Whether this LLM uses subscription-based authentication. "
            "Serialized so that subscription-specific request handling "
            "survives transport to a remote agent-server."
        ),
    )
    @property
    def is_subscription(self) -> bool:
        """Check if this LLM uses subscription-based authentication.

        Returns True when the LLM was created via `LLM.subscription_login()`,
        which uses the ChatGPT subscription Codex backend rather than the
        standard OpenAI API.

        Returns:
            bool: True if using subscription-based transport, False otherwise.
        """
        return self._is_subscription

    @model_validator(mode="wrap")
    @classmethod
    def _restore_is_subscription(cls, data, handler):
        """Restore the subscription flag when validating serialized data.

        ``is_subscription`` is a computed field backed by the private
        ``_is_subscription`` attribute, so plain validation would drop it.
        Without this, an LLM created via ``LLM.subscription_login()`` loses
        its subscription-specific request handling (streaming exemption,
        Codex system prompt transform, reasoning-item stripping) after a
        dump/validate round trip - e.g. when shipped to a remote
        agent-server.
        """
        llm = handler(data)
        if isinstance(data, dict) and data.get("is_subscription"):
            llm._is_subscription = True
        return llm

    def restore_metrics(self, metrics: Metrics) -> None:
        # Only used by ConversationStats to seed metrics
        self._metrics = metrics
        # Keep telemetry in sync so post-resume LLM calls record into
        # the restored metrics object, not the stale one from __init__.
        if self._telemetry is not None:
            self._telemetry.metrics = metrics

    def reset_metrics(self) -> None:
        """Reset metrics and telemetry to fresh instances.

        This is used by the LLMRegistry to ensure each registered LLM has
        independent metrics, preventing metrics from being shared between
        LLMs that were created via model_copy().

        When an LLM is copied (e.g., to create a condenser LLM from an agent LLM),
        Pydantic's model_copy() does a shallow copy of private attributes by default,
        causing the original and copied LLM to share the same Metrics object.
        This method allows the registry to fix this by creating fresh metrics and
        telemetry immediately, so the copied LLM is ready for the next completion
        call even if callers do not access ``metrics``/``telemetry`` first.
        """
        self._metrics = Metrics(model_name=self.model)
        self._telemetry = Telemetry(
            model_name=self.model,
            log_enabled=self.log_completions,
            log_dir=self.log_completions_folder if self.log_completions else None,
            input_cost_per_token=self.input_cost_per_token,
            output_cost_per_token=self.output_cost_per_token,
            metrics=self._metrics,
        )

    def _handle_error(
        self,
        error: Exception,
        fallback_call_fn: Callable[[LLM], LLMResponse],
    ) -> LLMResponse:
        """Handle an error from completion/responses: try fallback, then map and raise.

        Must be called from within an except block. Either returns an
        LLMResponse (fallback succeeded) or re-raises (mapped or original).
        """
        assert self._telemetry is not None
        self._telemetry.on_error(error)
        if self.fallback_strategy and self.fallback_strategy.should_fallback(error):
            result = self.fallback_strategy.try_fallback(
                primary_model=self.model,
                primary_error=error,
                primary_metrics=self.metrics,
                call_fn=fallback_call_fn,
            )
            if result is not None:
                return result
        mapped = map_provider_exception(error)
        if mapped is not error:
            raise mapped from error
        raise

    async def _ahandle_error(
        self,
        error: Exception,
        fallback_call_fn: Callable[[LLM], LLMResponse],
    ) -> LLMResponse:
        """Async variant of :meth:`_handle_error`.

        The *fallback_call_fn* is synchronous (it calls the fallback LLM's
        sync ``completion``/``responses``), so the fallback attempt is
        offloaded to a thread via :func:`asyncio.loop.run_in_executor` to
        avoid blocking the event loop.
        """
        import asyncio

        assert self._telemetry is not None
        self._telemetry.on_error(error)
        if self.fallback_strategy and self.fallback_strategy.should_fallback(error):
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                self.fallback_strategy.try_fallback,
                self.model,
                error,
                self.metrics,
                fallback_call_fn,
            )
            if result is not None:
                return result
        mapped = map_provider_exception(error)
        if mapped is not error:
            raise mapped from error
        raise

    # =========================================================================
    # Shared helpers for completion / acompletion / responses / aresponses
    # =========================================================================

    def _make_retry_decorator(
        self,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Return a configured retry decorator using this LLM's retry settings."""
        return self.retry_decorator(
            num_retries=self.num_retries,
            retry_exceptions=LLM_RETRY_EXCEPTIONS,
            retry_min_wait=self.retry_min_wait,
            retry_max_wait=self.retry_max_wait,
            retry_multiplier=self.retry_multiplier,
            retry_listener=self._retry_listener_fn,
        )

    def _build_completion_result(self, resp: ModelResponse) -> LLMResponse:
        """Convert a raw :class:`ModelResponse` into an :class:`LLMResponse`."""
        first_choice = resp["choices"][0]
        message = Message.from_llm_chat_message(first_choice["message"])
        return LLMResponse(
            message=message,
            metrics=self.metrics.get_snapshot(),
            raw_response=resp,
        )

    def _build_responses_result(self, resp: ResponsesAPIResponse) -> LLMResponse:
        """Convert a raw :class:`ResponsesAPIResponse` into an :class:`LLMResponse`."""
        output_seq = cast(Sequence[Any], resp.output or [])
        message = Message.from_llm_responses_output(output_seq)
        return LLMResponse(
            message=message,
            metrics=self.metrics.get_snapshot(),
            raw_response=resp,
        )

    def _build_responses_call_kwargs(
        self,
        input_items: list[dict[str, Any]],
        instructions: str | None,
        resp_tools: list[Any] | None,
        final_kwargs: dict[str, Any],
        auth_values: tuple[str | None, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Build the shared kwargs dict for litellm_responses / litellm_aresponses."""
        typed_input: ResponseInputParam | str = (
            cast(ResponseInputParam, input_items) if input_items else ""
        )
        api_key_value, subscription_headers = (
            auth_values if auth_values is not None else self._get_litellm_auth_values()
        )
        if subscription_headers:
            final_kwargs = {
                **final_kwargs,
                "extra_headers": {
                    **(final_kwargs.get("extra_headers") or {}),
                    **subscription_headers,
                },
            }
        return {
            **self._litellm_call_kwargs(),
            "input": typed_input,
            "instructions": instructions,
            "tools": resp_tools,
            "api_key": api_key_value,
            "api_version": self.api_version,
            "timeout": self.timeout,
            "drop_params": self.drop_params,
            "seed": self.seed,
            **self._aws_kwargs(),
            **final_kwargs,
        }

    def _process_stream_event(
        self, event: Any, *, emit_deltas: bool = True
    ) -> tuple[Any | None, ModelResponseStream | None]:
        """Extract output item and delta chunk from a Responses stream event.

        Args:
            event: A single Responses streaming event.
            emit_deltas: When ``False`` the delta chunk is never built — skip
                the allocation when there is no stream callback to receive it.

        Returns:
            (output_item, delta_chunk) — either or both may be ``None``.
        """
        output_item: Any | None = None
        delta_chunk: ModelResponseStream | None = None

        # Collect finished output items
        evt_type = getattr(event, "type", None)
        if evt_type == ResponsesAPIStreamEvents.OUTPUT_ITEM_DONE:
            item = getattr(event, "item", None)
            if item is not None:
                output_item = item

        if emit_deltas and isinstance(
            event,
            (
                OutputTextDeltaEvent,
                RefusalDeltaEvent,
                ReasoningSummaryTextDeltaEvent,
            ),
        ):
            delta = event.delta
            if delta:
                delta_chunk = ModelResponseStream(
                    choices=[StreamingChoices(delta=Delta(content=delta))]
                )

        return output_item, delta_chunk

    def _finalize_stream_response(
        self,
        completed_response: Any,
        collected_output_items: list[Any],
    ) -> ResponsesAPIResponse:
        """Validate and patch the completed response from a Responses stream.

        Raises:
            LLMNoResponseError: If the stream finished without a completed
                response or with an unexpected event type.
        """
        if completed_response is None:
            raise LLMNoResponseError(
                "Responses stream finished without a completed response"
            )
        if not isinstance(completed_response, ResponseCompletedEvent):
            raise LLMNoResponseError(
                f"Unexpected completed event: {type(completed_response)}"
            )

        completed_resp = completed_response.response
        # Patch empty output with items collected from stream
        if not completed_resp.output and collected_output_items:
            completed_resp.output = collected_output_items

        assert self._telemetry is not None
        self._telemetry.on_response(completed_resp)
        return completed_resp

    def _prepare_completion_params(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None,
        add_security_risk_prediction: bool,
        kwargs: dict[str, Any],
    ) -> tuple[
        list[dict[str, Any]],
        list[ChatCompletionToolParam],
        bool,
        dict[str, Any],
        dict[str, Any],
    ]:
        """Shared setup for :meth:`completion`.

        Returns:
            (formatted_messages, cc_tools, use_mock_tools, call_kwargs,
             telemetry_ctx)
        """
        formatted_messages = self.format_messages_for_llm(messages)
        return self._finalize_completion_params(
            formatted_messages, tools, add_security_risk_prediction, kwargs
        )

    async def _aprepare_completion_params(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None,
        add_security_risk_prediction: bool,
        kwargs: dict[str, Any],
    ) -> tuple[
        list[dict[str, Any]],
        list[ChatCompletionToolParam],
        bool,
        dict[str, Any],
        dict[str, Any],
    ]:
        """Async variant of :meth:`_prepare_completion_params`.

        Uses :meth:`aformat_messages_for_llm` so the (potentially blocking)
        image-inlining pass is offloaded to a worker thread instead of running
        on the event loop.
        """
        formatted_messages = await self.aformat_messages_for_llm(messages)
        return self._finalize_completion_params(
            formatted_messages, tools, add_security_risk_prediction, kwargs
        )

    def _finalize_completion_params(
        self,
        formatted_messages: list[dict[str, Any]],
        tools: Sequence[ToolDefinition] | None,
        add_security_risk_prediction: bool,
        kwargs: dict[str, Any],
    ) -> tuple[
        list[dict[str, Any]],
        list[ChatCompletionToolParam],
        bool,
        dict[str, Any],
        dict[str, Any],
    ]:
        """Finalize chat completion params from already-formatted messages.

        Shared post-formatting steps for :meth:`_prepare_completion_params`
        and :meth:`_aprepare_completion_params`: tool conversion, mock-tool
        prompt substitution, kwargs normalization and telemetry context.
        """
        # Defensive copy — this method mutates kwargs (e.g. kwargs["tools"])
        # and the caller should not observe those side-effects.
        kwargs = dict(kwargs)

        # 2) choose function-calling strategy
        use_native_fc = self.native_tool_calling
        original_fncall_msgs = copy.deepcopy(formatted_messages)

        # Convert Tool objects to ChatCompletionToolParam once here
        cc_tools: list[ChatCompletionToolParam] = []
        if tools:
            cc_tools = [
                t.to_openai_tool(
                    add_security_risk_prediction=add_security_risk_prediction,
                )
                for t in tools
            ]

        use_mock_tools = self.should_mock_tool_calls(cc_tools)
        if use_mock_tools:
            logger.debug(
                "LLM.completion: mocking function-calling via prompt "
                f"for model {self.model}"
            )
            formatted_messages, kwargs = self.pre_request_prompt_mock(
                formatted_messages,
                cc_tools or [],
                kwargs,
                include_security_params=add_security_risk_prediction,
            )

        # 3) normalize provider params
        # Only pass tools when native FC is active
        kwargs["tools"] = cc_tools if (bool(cc_tools) and use_native_fc) else None
        has_tools_flag = bool(cc_tools) and use_native_fc
        # Behavior-preserving: delegate to select_chat_options
        call_kwargs = select_chat_options(self, kwargs, has_tools=has_tools_flag)

        # 3a) joint input/output budget clamp (e.g. Bedrock-Anthropic)
        call_kwargs = self._clamp_max_tokens_for_joint_budget(
            call_kwargs,
            formatted_messages,
            [] if use_mock_tools else cc_tools,
        )

        # 4) request context for telemetry (always include context_window for metrics)
        # Always pass context_window so metrics are tracked even when
        # logging is disabled.
        telemetry = self.telemetry
        telemetry_ctx: dict[str, Any] = {
            "context_window": self.effective_max_input_tokens or 0
        }
        if telemetry.log_enabled:
            telemetry_ctx.update(
                {
                    "messages": formatted_messages[:],  # already simple dicts
                    "tools": tools,
                    "kwargs": {k: v for k, v in call_kwargs.items()},
                }
            )
            if tools and not use_native_fc:
                telemetry_ctx["raw_messages"] = original_fncall_msgs

        return (
            formatted_messages,
            cc_tools,
            use_mock_tools,
            call_kwargs,
            telemetry_ctx,
        )

    def _prepare_responses_params(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None,
        include: list[str] | None,
        store: bool | None,
        add_security_risk_prediction: bool,
        kwargs: dict[str, Any],
    ) -> tuple[
        str | None,
        list[dict[str, Any]],
        list[Any] | None,
        dict[str, Any],
        dict[str, Any],
    ]:
        """Shared setup for :meth:`responses`.

        Returns:
            (instructions, input_items, resp_tools, call_kwargs,
             telemetry_ctx)
        """
        instructions, input_items = self.format_messages_for_responses(messages)
        return self._finalize_responses_params(
            instructions,
            input_items,
            tools,
            include,
            store,
            add_security_risk_prediction,
            kwargs,
        )

    async def _aprepare_responses_params(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None,
        include: list[str] | None,
        store: bool | None,
        add_security_risk_prediction: bool,
        kwargs: dict[str, Any],
    ) -> tuple[
        str | None,
        list[dict[str, Any]],
        list[Any] | None,
        dict[str, Any],
        dict[str, Any],
    ]:
        """Async variant of :meth:`_prepare_responses_params`.

        Uses :meth:`aformat_messages_for_responses` so the image-inlining
        pass runs off the event loop.
        """
        instructions, input_items = await self.aformat_messages_for_responses(messages)
        return self._finalize_responses_params(
            instructions,
            input_items,
            tools,
            include,
            store,
            add_security_risk_prediction,
            kwargs,
        )

    def _finalize_responses_params(
        self,
        instructions: str | None,
        input_items: list[dict[str, Any]],
        tools: Sequence[ToolDefinition] | None,
        include: list[str] | None,
        store: bool | None,
        add_security_risk_prediction: bool,
        kwargs: dict[str, Any],
    ) -> tuple[
        str | None,
        list[dict[str, Any]],
        list[Any] | None,
        dict[str, Any],
        dict[str, Any],
    ]:
        """Finalize Responses params from already-formatted inputs.

        Shared post-formatting steps for :meth:`_prepare_responses_params`
        and :meth:`_aprepare_responses_params`.
        """
        # Defensive copy — select_responses_options may mutate kwargs.
        kwargs = dict(kwargs)

        # Convert Tool objects to Responses ToolParam
        # (Responses path always supports function tools)
        resp_tools = (
            [
                t.to_responses_tool(
                    add_security_risk_prediction=add_security_risk_prediction,
                )
                for t in tools
            ]
            if tools
            else None
        )

        # Normalize/override Responses kwargs consistently
        call_kwargs = select_responses_options(
            self, kwargs, include=include, store=store
        )

        # Request context for telemetry (always include context_window for metrics)
        # Always pass context_window so metrics are tracked even when
        # logging is disabled.
        telemetry = self.telemetry
        telemetry_ctx: dict[str, Any] = {
            "context_window": self.effective_max_input_tokens or 0
        }
        if telemetry.log_enabled:
            telemetry_ctx.update(
                {
                    "llm_path": "responses",
                    "instructions": instructions,
                    "input": input_items[:],
                    "tools": tools,
                    "kwargs": {k: v for k, v in call_kwargs.items()},
                }
            )

        return instructions, input_items, resp_tools, call_kwargs, telemetry_ctx

    def _validate_chat_response(
        self,
        resp: ModelResponse,
        *,
        use_mock_tools: bool,
        formatted_messages: list[dict[str, Any]],
        cc_tools: list[ChatCompletionToolParam],
        add_security_risk_prediction: bool,
    ) -> ModelResponse:
        """Post-process a chat completion response inside the retry boundary.

        The raw (pre-mock) response is consumed internally by
        ``Telemetry.on_response`` and is not returned to the caller.

        Raises:
            LLMNoResponseError: If the response has no choices
                (Gemini sometimes returns empty choices; raising here
                inside the retry boundary ensures it is retried).
        """
        raw_resp: ModelResponse | None = None
        if use_mock_tools:
            raw_resp = copy.deepcopy(resp)
            resp = self.post_response_prompt_mock(
                resp,
                nonfncall_msgs=formatted_messages,
                tools=cc_tools,
                include_security_params=add_security_risk_prediction,
            )

        # 6) telemetry
        assert self._telemetry is not None
        self._telemetry.on_response(resp, raw_resp=raw_resp)

        # Ensure at least one choice.
        # Gemini sometimes returns empty choices; we raise LLMNoResponseError here
        # inside the retry boundary so it is retried.
        if not resp.get("choices") or len(resp["choices"]) < 1:
            raise LLMNoResponseError(
                "Response choices is less than 1. Response: " + str(resp)
            )
        return resp

    # =========================================================================
    # Chat Completion API
    # =========================================================================

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Generate a completion from the language model.

        This is the method for getting responses from the model via Completion API.
        It handles message formatting, tool calling, and response processing.

        Args:
            messages: List of conversation messages.
            tools: Optional list of tools available to the model.
            add_security_risk_prediction: Add security_risk field to tool schemas.
            on_token: Optional callback for streaming tokens.
            **kwargs: Additional arguments passed to the LLM API.

        Returns:
            LLMResponse containing the model's response and metadata.

        Note:
            Summary field is always added to tool schemas for transparency and
            explainability of agent actions.

        Raises:
            ValueError: If streaming is requested (not supported).

        Example:
            ```python
            from openhands.sdk.llm import Message, TextContent

            messages = [Message(role="user", content=[TextContent(text="Hello")])]
            response = llm.completion(messages)
            print(response.content)
            ```
        """
        _caller_kwargs = kwargs.copy()
        enable_streaming = bool(kwargs.get("stream", False)) or self.stream
        if enable_streaming:
            if on_token is None:
                raise ValueError("Streaming requires an on_token callback")
            kwargs["stream"] = True

        (
            formatted_messages,
            cc_tools,
            use_mock_tools,
            call_kwargs,
            telemetry_ctx,
        ) = self._prepare_completion_params(
            messages, tools, add_security_risk_prediction, kwargs
        )

        @self._make_retry_decorator()
        def _one_attempt(**retry_kwargs: Any) -> ModelResponse:
            assert self._telemetry is not None
            self._telemetry.on_request(telemetry_ctx=telemetry_ctx)
            final_kwargs = {**call_kwargs, **retry_kwargs}
            resp = self._transport_call(
                messages=formatted_messages,
                **final_kwargs,
                enable_streaming=enable_streaming,
                on_token=on_token,
            )
            resp = self._validate_chat_response(
                resp,
                use_mock_tools=use_mock_tools,
                formatted_messages=formatted_messages,
                cc_tools=cc_tools,
                add_security_risk_prediction=add_security_risk_prediction,
            )
            return resp

        try:
            return self._build_completion_result(_one_attempt())
        except Exception as e:
            # If the prompt cache content is too small for the provider's
            # minimum token threshold (e.g., Vertex AI requires ≥4096 tokens),
            # retry without prompt caching markers.
            if is_prompt_cache_too_small(e) and self.is_caching_prompt_active():
                logger.warning(
                    "Prompt cache content too small for provider minimum, "
                    "retrying without prompt caching"
                )
                no_cache_llm = self.model_copy(update={"caching_prompt": False})
                return no_cache_llm.completion(
                    messages,
                    tools,
                    add_security_risk_prediction=add_security_risk_prediction,
                    on_token=on_token,
                    **_caller_kwargs,
                )
            return self._handle_error(
                e,
                lambda fb: fb.completion(
                    messages,
                    tools,
                    add_security_risk_prediction=add_security_risk_prediction,
                    on_token=on_token,
                    **_caller_kwargs,
                ),
            )

    # =========================================================================
    # Async Chat Completion API
    # =========================================================================
    async def acompletion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: AnyTokenCallbackType | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Async variant of :meth:`completion`.

        Uses ``litellm.acompletion`` under the hood, freeing the event loop
        while waiting for the LLM provider response.
        """
        _caller_kwargs = kwargs.copy()
        enable_streaming = bool(kwargs.get("stream", False)) or self.stream
        if enable_streaming:
            if on_token is None:
                raise ValueError("Streaming requires an on_token callback")
            kwargs["stream"] = True

        (
            formatted_messages,
            cc_tools,
            use_mock_tools,
            call_kwargs,
            telemetry_ctx,
        ) = await self._aprepare_completion_params(
            messages, tools, add_security_risk_prediction, kwargs
        )

        @self._make_retry_decorator()
        async def _one_attempt(**retry_kwargs: Any) -> ModelResponse:
            assert self._telemetry is not None
            self._telemetry.on_request(telemetry_ctx=telemetry_ctx)
            final_kwargs = {**call_kwargs, **retry_kwargs}
            resp = await self._atransport_call(
                messages=formatted_messages,
                **final_kwargs,
                enable_streaming=enable_streaming,
                on_token=on_token,
            )
            resp = self._validate_chat_response(
                resp,
                use_mock_tools=use_mock_tools,
                formatted_messages=formatted_messages,
                cc_tools=cc_tools,
                add_security_risk_prediction=add_security_risk_prediction,
            )
            return resp

        try:
            return self._build_completion_result(await _one_attempt())
        except Exception as e:
            # If the prompt cache content is too small for the provider's
            # minimum token threshold (e.g., Vertex AI requires ≥4096 tokens),
            # retry without prompt caching markers.
            if is_prompt_cache_too_small(e) and self.is_caching_prompt_active():
                logger.warning(
                    "Prompt cache content too small for provider minimum, "
                    "retrying without prompt caching"
                )
                no_cache_llm = self.model_copy(update={"caching_prompt": False})
                return await no_cache_llm.acompletion(
                    messages,
                    tools,
                    add_security_risk_prediction=add_security_risk_prediction,
                    on_token=on_token,
                    **_caller_kwargs,
                )
            # Fallback is synchronous; cast the token callback since the
            # fallback LLM's sync path accepts TokenCallbackType.
            _fb_token = cast("TokenCallbackType | None", on_token)
            return await self._ahandle_error(
                e,
                lambda fb: fb.completion(
                    messages,
                    tools,
                    add_security_risk_prediction=add_security_risk_prediction,
                    on_token=_fb_token,
                    **_caller_kwargs,
                ),
            )

    # =========================================================================
    # Responses API (v1)
    # =========================================================================
    def responses(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        include: list[str] | None = None,
        store: bool | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Alternative invocation path using OpenAI Responses API via LiteLLM.

        Maps Message[] -> (instructions, input[]) and returns LLMResponse.

        Args:
            messages: List of conversation messages
            tools: Optional list of tools available to the model
            include: Optional list of fields to include in response
            store: Whether to store the conversation
            add_security_risk_prediction: Add security_risk field to tool schemas
            on_token: Optional callback for streaming deltas
            **kwargs: Additional arguments passed to the API

        Note:
            Summary field is always added to tool schemas for transparency and
            explainability of agent actions.
        """
        _caller_kwargs = kwargs.copy()
        user_enable_streaming = bool(kwargs.get("stream", False)) or self.stream
        if user_enable_streaming:
            # We allow on_token to be None for subscription mode
            if on_token is None and not self.is_subscription:
                raise ValueError("Streaming requires an on_token callback")
            kwargs["stream"] = True

        (
            instructions,
            input_items,
            resp_tools,
            call_kwargs,
            telemetry_ctx,
        ) = self._prepare_responses_params(
            messages, tools, include, store, add_security_risk_prediction, kwargs
        )

        @self._make_retry_decorator()
        def _one_attempt(**retry_kwargs: Any) -> ResponsesAPIResponse:
            assert self._telemetry is not None
            self._telemetry.on_request(telemetry_ctx=telemetry_ctx)
            final_kwargs = {**call_kwargs, **retry_kwargs}
            with self._litellm_modify_params_ctx(self.modify_params):
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=DeprecationWarning)
                    litellm_kwargs = self._build_responses_call_kwargs(
                        input_items, instructions, resp_tools, final_kwargs
                    )
                    ret = litellm_responses(**litellm_kwargs)

                    if isinstance(ret, ResponsesAPIResponse):
                        if user_enable_streaming:
                            logger.warning(
                                "Responses streaming was requested, but the "
                                "provider returned a non-streaming response; "
                                "no on_token deltas will be emitted."
                            )
                        self._telemetry.on_response(ret)
                        return ret

                    # When stream=True, LiteLLM returns a streaming
                    # iterator rather than a single ResponsesAPIResponse.
                    # Drain the iterator and use the completed response.
                    if final_kwargs.get("stream", False):
                        if not isinstance(ret, SyncResponsesAPIStreamingIterator):
                            raise AssertionError(
                                f"Expected Responses stream iterator, got {type(ret)}"
                            )
                        stream_callback = on_token if user_enable_streaming else None
                        # Collect output items from streaming events.
                        # Some endpoints (e.g., Codex subscription) send
                        # output items as separate events but the final
                        # response.completed event has output=[].  We
                        # accumulate them here and patch the completed
                        # response if needed.
                        collected_output_items: list[Any] = []
                        for event in ret:
                            if event is None:
                                continue
                            output_item, delta_chunk = self._process_stream_event(
                                event, emit_deltas=stream_callback is not None
                            )
                            if output_item is not None:
                                collected_output_items.append(output_item)
                            if stream_callback is not None and delta_chunk is not None:
                                stream_callback(delta_chunk)

                        return self._finalize_stream_response(
                            ret.completed_response, collected_output_items
                        )

                    raise AssertionError(
                        f"Expected ResponsesAPIResponse, got {type(ret)}"
                    )

        try:
            return self._build_responses_result(_one_attempt())
        except Exception as e:
            # If the prompt cache content is too small for the provider's
            # minimum token threshold (e.g., Vertex AI requires ≥4096 tokens),
            # retry without prompt caching markers.
            if is_prompt_cache_too_small(e) and self.is_caching_prompt_active():
                logger.warning(
                    "Prompt cache content too small for provider minimum, "
                    "retrying without prompt caching"
                )
                no_cache_llm = self.model_copy(update={"caching_prompt": False})
                return no_cache_llm.responses(
                    messages,
                    tools,
                    include,
                    store,
                    add_security_risk_prediction=add_security_risk_prediction,
                    on_token=on_token,
                    **_caller_kwargs,
                )
            return self._handle_error(
                e,
                lambda fb: fb.responses(
                    messages,
                    tools,
                    include,
                    store,
                    add_security_risk_prediction=add_security_risk_prediction,
                    on_token=on_token,
                    **_caller_kwargs,
                ),
            )

    # =========================================================================
    # Async Responses API
    # =========================================================================
    async def aresponses(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        include: list[str] | None = None,
        store: bool | None = None,
        add_security_risk_prediction: bool = False,
        on_token: AnyTokenCallbackType | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Async variant of :meth:`responses`.

        Uses ``litellm.aresponses`` under the hood, freeing the event loop
        while waiting for the LLM provider response.
        """
        _caller_kwargs = kwargs.copy()
        user_enable_streaming = bool(kwargs.get("stream", False)) or self.stream
        if user_enable_streaming:
            # We allow on_token to be None for subscription mode
            if on_token is None and not self.is_subscription:
                raise ValueError("Streaming requires an on_token callback")
            kwargs["stream"] = True

        (
            instructions,
            input_items,
            resp_tools,
            call_kwargs,
            telemetry_ctx,
        ) = await self._aprepare_responses_params(
            messages, tools, include, store, add_security_risk_prediction, kwargs
        )

        @self._make_retry_decorator()
        async def _one_attempt(
            **retry_kwargs: Any,
        ) -> ResponsesAPIResponse:
            assert self._telemetry is not None
            self._telemetry.on_request(telemetry_ctx=telemetry_ctx)
            final_kwargs = {**call_kwargs, **retry_kwargs}
            with self._litellm_modify_params_ctx(self.modify_params):
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=DeprecationWarning)
                    auth_values = await self._aget_litellm_auth_values()
                    litellm_kwargs = self._build_responses_call_kwargs(
                        input_items,
                        instructions,
                        resp_tools,
                        final_kwargs,
                        auth_values=auth_values,
                    )
                    ret = await litellm_aresponses(**litellm_kwargs)

                    if isinstance(ret, ResponsesAPIResponse):
                        if user_enable_streaming:
                            logger.warning(
                                "Responses streaming was requested, but the "
                                "provider returned a non-streaming response; "
                                "no on_token deltas will be emitted."
                            )
                        self._telemetry.on_response(ret)
                        return ret

                    # When stream=True, LiteLLM returns a streaming
                    # iterator rather than a single ResponsesAPIResponse.
                    # Drain the iterator and use the completed response.
                    if final_kwargs.get("stream", False):
                        if not isinstance(ret, ResponsesAPIStreamingIterator):
                            raise AssertionError(
                                "Expected Responses async stream "
                                f"iterator, got {type(ret)}"
                            )
                        stream_cb = on_token if user_enable_streaming else None
                        # Collect output items from streaming events.
                        # Some endpoints (e.g., Codex subscription) send
                        # output items as separate events but the final
                        # response.completed event has output=[].  We
                        # accumulate them here and patch the completed
                        # response if needed.
                        collected_output_items: list[Any] = []
                        async for event in ret:
                            if event is None:
                                continue
                            output_item, delta_chunk = self._process_stream_event(
                                event, emit_deltas=stream_cb is not None
                            )
                            if output_item is not None:
                                collected_output_items.append(output_item)
                            if stream_cb is not None and delta_chunk is not None:
                                await _invoke_token_callback(stream_cb, delta_chunk)

                        return self._finalize_stream_response(
                            ret.completed_response, collected_output_items
                        )

                    raise AssertionError(
                        f"Expected ResponsesAPIResponse, got {type(ret)}"
                    )

        try:
            return self._build_responses_result(await _one_attempt())
        except Exception as e:
            # If the prompt cache content is too small for the provider's
            # minimum token threshold (e.g., Vertex AI requires ≥4096 tokens),
            # retry without prompt caching markers.
            if is_prompt_cache_too_small(e) and self.is_caching_prompt_active():
                logger.warning(
                    "Prompt cache content too small for provider minimum, "
                    "retrying without prompt caching"
                )
                no_cache_llm = self.model_copy(update={"caching_prompt": False})
                return await no_cache_llm.aresponses(
                    messages,
                    tools,
                    include,
                    store,
                    add_security_risk_prediction=add_security_risk_prediction,
                    on_token=on_token,
                    **_caller_kwargs,
                )
            _fb_token = cast("TokenCallbackType | None", on_token)
            return await self._ahandle_error(
                e,
                lambda fb: fb.responses(
                    messages,
                    tools,
                    include,
                    store,
                    add_security_risk_prediction=add_security_risk_prediction,
                    on_token=_fb_token,
                    **_caller_kwargs,
                ),
            )

    # =========================================================================
    # Transport + helpers
    # =========================================================================

    def _litellm_call_kwargs(self) -> LiteLLMCallKwargs:
        return litellm_call_kwargs(self.model, self.base_url)

    def _infer_litellm_provider(self) -> str | None:
        if self._litellm_provider is not None:
            return self._litellm_provider

        call_kwargs = self._litellm_call_kwargs()
        provider = infer_litellm_provider(
            model=call_kwargs["model"],
            api_base=call_kwargs["api_base"],
        )
        self._litellm_provider = provider
        return provider

    def _infer_model_info_provider(self) -> str | None:
        if self._model_info is not None:
            provider = self._model_info.get("litellm_provider")
            if isinstance(provider, str) and provider:
                return provider

        return self._infer_litellm_provider()

    def _subscription_headers_from_credentials(
        self, auth: Any, credentials: Any
    ) -> dict[str, str]:
        account_id = auth.extract_chatgpt_account_id(credentials)
        if not account_id:
            return {}
        return {"chatgpt-account-id": account_id}

    def _subscription_api_key_from_credentials(self, credentials: Any) -> str:
        return credentials.access_token

    def _normalize_litellm_api_key_value(self, api_key_value: str | None) -> str | None:
        # LiteLLM treats api_key for Bedrock as an AWS bearer token.
        # Passing a non-Bedrock key (e.g. OpenAI/Anthropic) can cause Bedrock
        # to reject the request with an "Invalid API Key format" error.
        # For IAM/SigV4 auth (the default Bedrock path), do not forward api_key.
        if api_key_value is not None and self._infer_litellm_provider() == "bedrock":
            return None
        return api_key_value

    def _get_litellm_auth_values(self) -> tuple[str | None, dict[str, str]]:
        api_key_value: str | None = None
        extra_headers: dict[str, str] = {}
        if self.is_subscription:
            from openhands.sdk.llm.auth.openai import OpenAISubscriptionAuth

            auth = OpenAISubscriptionAuth(
                credential_store=self._subscription_credential_store
            )
            credentials = self._subscription_credentials
            if credentials is None or credentials.is_expired():
                credentials = auth.refresh_if_needed_sync()
            if credentials is None:
                credentials = self._subscription_credentials
            if credentials is None:
                raise ValueError("OpenAI subscription login is required")
            api_key_value = self._subscription_api_key_from_credentials(credentials)
            extra_headers = self._subscription_headers_from_credentials(
                auth, credentials
            )
        elif self.api_key:
            assert isinstance(self.api_key, SecretStr)
            api_key_value = self.api_key.get_secret_value()

        return self._normalize_litellm_api_key_value(api_key_value), extra_headers

    def _get_litellm_api_key_value(self) -> str | None:
        api_key_value, _ = self._get_litellm_auth_values()
        return api_key_value

    async def _aget_litellm_auth_values(self) -> tuple[str | None, dict[str, str]]:
        if not self.is_subscription:
            return self._get_litellm_auth_values()

        from openhands.sdk.llm.auth.openai import OpenAISubscriptionAuth

        auth = OpenAISubscriptionAuth(
            credential_store=self._subscription_credential_store
        )
        credentials = await auth.refresh_if_needed()
        if credentials is None:
            credentials = self._subscription_credentials
        if credentials is None:
            raise ValueError("OpenAI subscription login is required")
        api_key_value = self._normalize_litellm_api_key_value(
            self._subscription_api_key_from_credentials(credentials)
        )
        return api_key_value, self._subscription_headers_from_credentials(
            auth, credentials
        )

    async def _aget_litellm_api_key_value(self) -> str | None:
        api_key_value, _ = await self._aget_litellm_auth_values()
        return api_key_value

    @contextmanager
    def _transport_ctx(self):
        """Guard a litellm transport call.

        ``litellm.modify_params`` is GLOBAL, so it is guarded for thread-safety,
        and the noisy provider/litellm warnings are filtered out for the call.
        """
        with self._litellm_modify_params_ctx(self.modify_params):
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", category=DeprecationWarning, module="httpx.*"
                )
                warnings.filterwarnings(
                    "ignore",
                    message=r".*content=.*upload.*",
                    category=DeprecationWarning,
                )
                warnings.filterwarnings(
                    "ignore",
                    message="There is no current event loop",
                    category=DeprecationWarning,
                )
                warnings.filterwarnings("ignore", category=UserWarning)
                warnings.filterwarnings(
                    "ignore",
                    category=DeprecationWarning,
                    message="Accessing the 'model_fields' attribute.*",
                )
                yield

    def _prepare_transport_kwargs(
        self,
        *,
        messages: list[dict[str, Any]],
        enable_streaming: bool,
        auth_values: tuple[str | None, dict[str, str]] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Build the keyword arguments for a litellm (a)completion call."""
        provider = self._infer_litellm_provider()
        assert_vertex_sdk_available(provider)

        # When streaming, request usage in the final chunk so that detailed
        # token breakdowns (prompt_tokens_details with cached_tokens, etc.) are
        # not silently discarded by litellm's streaming handler.
        if enable_streaming:
            kwargs.setdefault("stream_options", {"include_usage": True})
        api_key_value, subscription_headers = (
            auth_values if auth_values is not None else self._get_litellm_auth_values()
        )
        if subscription_headers:
            kwargs["extra_headers"] = {
                **(kwargs.get("extra_headers") or {}),
                **subscription_headers,
            }
        return {
            **self._litellm_call_kwargs(),
            "api_key": api_key_value,
            "api_version": self.api_version,
            "timeout": self.timeout,
            "drop_params": self.drop_params,
            "seed": self.seed,
            "messages": messages,
            **self._aws_kwargs(),
            **kwargs,
        }

    def _transport_call(
        self,
        *,
        messages: list[dict[str, Any]],
        enable_streaming: bool = False,
        on_token: TokenCallbackType | None = None,
        **kwargs,
    ) -> ModelResponse:
        with self._transport_ctx():
            ret = litellm_completion(
                **self._prepare_transport_kwargs(
                    messages=messages, enable_streaming=enable_streaming, **kwargs
                )
            )
            if enable_streaming and on_token is not None:
                assert isinstance(ret, CustomStreamWrapper)
                chunks: list[ModelResponseStream] = []
                for chunk in ret:
                    on_token(chunk)
                    chunks.append(chunk)
                ret = litellm.stream_chunk_builder(chunks, messages=messages)

            assert isinstance(ret, ModelResponse), (
                f"Expected ModelResponse, got {type(ret)}"
            )
            return ret

    async def _atransport_call(
        self,
        *,
        messages: list[dict[str, Any]],
        enable_streaming: bool = False,
        on_token: AnyTokenCallbackType | None = None,
        **kwargs,
    ) -> ModelResponse:
        """Async variant of :meth:`_transport_call`."""
        auth_values = await self._aget_litellm_auth_values()
        with self._transport_ctx():
            ret = await litellm_acompletion(
                **self._prepare_transport_kwargs(
                    messages=messages,
                    enable_streaming=enable_streaming,
                    auth_values=auth_values,
                    **kwargs,
                )
            )
            if enable_streaming and on_token is not None:
                assert isinstance(ret, CustomStreamWrapper)
                chunks: list[ModelResponseStream] = []
                async for chunk in ret:
                    await _invoke_token_callback(on_token, chunk)
                    chunks.append(chunk)
                ret = litellm.stream_chunk_builder(chunks, messages=messages)

            assert isinstance(ret, ModelResponse), (
                f"Expected ModelResponse, got {type(ret)}"
            )
            return ret

    @contextmanager
    def _litellm_modify_params_ctx(self, flag: bool):
        with self._litellm_modify_params_lock:
            old = getattr(litellm, "modify_params", None)
            try:
                litellm.modify_params = flag
                yield
            finally:
                litellm.modify_params = old

    # =========================================================================
    # Capabilities, formatting, and info
    # =========================================================================
    def _model_name_for_capabilities(self) -> str:
        """Return canonical name for capability lookups (e.g., vision support)."""
        return self.model_canonical_name or self.model

    def _provider_has_joint_token_budget(self) -> bool:
        """Whether the provider enforces input + max_tokens <= context_window.

        Some providers (notably AWS Bedrock for Anthropic models) share a single
        token window between input and requested output. Direct providers
        (Anthropic, OpenAI, etc.) have independent input/output windows.
        """
        provider = self._infer_model_info_provider() or ""
        return any(provider.startswith(p) for p in _JOINT_BUDGET_PROVIDER_PREFIXES)

    def _clamp_max_tokens_for_joint_budget(
        self,
        call_kwargs: dict[str, Any],
        formatted_messages: list[dict[str, Any]],
        cc_tools: list[ChatCompletionToolParam],
    ) -> dict[str, Any]:
        """Clamp per-request output budget for joint-window providers.

        For providers like Bedrock-Anthropic, ``input_tokens + max_tokens`` must
        not exceed the context window. Our injected default for ``max_tokens`` is
        the model's full ``max_output_tokens`` (e.g. 64k for Sonnet 4.5), which
        for any input larger than roughly ``context_window - max_output_tokens``
        will fail with "Input is too long for requested model" -- even when the
        actual response would be small. Clamp to the headroom that actually
        remains, with a safety margin and a floor so we never send ``0``.

        For non-joint providers the parameters are independent budgets and we
        leave the kwargs untouched.
        """
        if not self._provider_has_joint_token_budget():
            return call_kwargs

        context_window = self.effective_max_input_tokens
        if not context_window:
            return call_kwargs

        # Both keys may appear depending on routing (Azure / extended thinking
        # use ``max_tokens``; everything else uses ``max_completion_tokens``).
        budget_key = next(
            (k for k in ("max_tokens", "max_completion_tokens") if k in call_kwargs),
            None,
        )
        if budget_key is None:
            return call_kwargs
        current_budget = call_kwargs.get(budget_key)
        if not isinstance(current_budget, int) or current_budget <= 0:
            return call_kwargs

        try:
            input_tokens = int(
                token_counter(
                    model=self.model,
                    messages=formatted_messages,
                    tools=cc_tools or None,
                    custom_tokenizer=self._tokenizer,
                )
            )
        except Exception:
            logger.debug(
                "Joint-budget clamp: token_counter failed for %s; "
                "leaving max_tokens unchanged",
                self.model,
                exc_info=True,
            )
            return call_kwargs

        headroom = context_window - input_tokens - JOINT_BUDGET_SAFETY_MARGIN_TOKENS
        clamped = max(min(current_budget, headroom), JOINT_BUDGET_MIN_OUTPUT_TOKENS)

        if clamped >= current_budget:
            return call_kwargs

        if headroom < JOINT_BUDGET_MIN_OUTPUT_TOKENS:
            logger.warning(
                "Input tokens (%s) leave only %s tokens of headroom in the %s "
                "context window for %s; sending the floor of %s. The provider "
                "may still reject this request -- consider condensing history.",
                input_tokens,
                max(headroom, 0),
                context_window,
                self.model,
                JOINT_BUDGET_MIN_OUTPUT_TOKENS,
            )
        else:
            logger.debug(
                "Clamping %s from %s to %s for %s "
                "(input_tokens=%s, context_window=%s, joint-budget provider)",
                budget_key,
                current_budget,
                clamped,
                self.model,
                input_tokens,
                context_window,
            )
        return {**call_kwargs, budget_key: clamped}

    def _init_model_info_and_caps(self) -> None:
        self._model_info = get_litellm_model_info(
            secret_api_key=self.api_key,
            base_url=self.base_url,
            model=self._model_name_for_capabilities(),
        )

        self._effective_max_input_tokens = self.max_input_tokens
        if (
            self._effective_max_input_tokens is None
            and self._model_info is not None
            and isinstance(self._model_info.get("max_input_tokens"), int)
        ):
            self._effective_max_input_tokens = self._model_info.get("max_input_tokens")

        # Validate context window size
        self._validate_context_window_size()

        effective_max_output_tokens = self.max_output_tokens
        if effective_max_output_tokens is None:
            if any(
                m in self.model
                for m in [
                    "claude-3-7-sonnet",
                    "claude-sonnet-4",
                    "kimi-k2-thinking",
                ]
            ):
                effective_max_output_tokens = (
                    64000  # practical cap (litellm may allow 128k with header)
                )
                logger.debug(
                    f"Setting effective max_output_tokens to "
                    f"{effective_max_output_tokens} "
                    f"for {self.model}"
                )
            elif self._model_info is not None:
                if isinstance(self._model_info.get("max_output_tokens"), int):
                    effective_max_output_tokens = self._model_info.get(
                        "max_output_tokens"
                    )
                    # Guard: if max_output_tokens >= the context window,
                    # requesting that many output tokens would leave zero
                    # room for input and strict providers (e.g. AWS Bedrock)
                    # will reject every call. Halve it so input has
                    # headroom. We check both max_input_tokens and
                    # max_tokens since either may represent the context
                    # window depending on the provider.
                    context_window = (
                        self.effective_max_input_tokens
                        or self._model_info.get("max_tokens")
                    )
                    if (
                        context_window is not None
                        and effective_max_output_tokens is not None
                        and effective_max_output_tokens >= context_window
                    ):
                        capped = effective_max_output_tokens // 2
                        logger.debug(
                            "Capping max_output_tokens from %s to %s "
                            "for %s (max_output_tokens >= context "
                            "window %s)",
                            effective_max_output_tokens,
                            capped,
                            self.model,
                            context_window,
                        )
                        effective_max_output_tokens = capped
                elif isinstance(self._model_info.get("max_tokens"), int):
                    # 'max_tokens' is ambiguous: some providers use it for total
                    # context window, not output limit. Cap it to avoid requesting
                    # output that exceeds the context window.
                    max_tokens_value = self._model_info.get("max_tokens")
                    assert isinstance(max_tokens_value, int)  # for type checker
                    effective_max_output_tokens = min(
                        max_tokens_value, DEFAULT_MAX_OUTPUT_TOKENS_CAP
                    )
                    if max_tokens_value > DEFAULT_MAX_OUTPUT_TOKENS_CAP:
                        logger.debug(
                            "Capping max_output_tokens from %s to %s for %s "
                            "(max_tokens may be context window, not output)",
                            max_tokens_value,
                            effective_max_output_tokens,
                            self.model,
                        )

        if "o3" in self.model:
            o3_limit = 100000
            if (
                effective_max_output_tokens is None
                or effective_max_output_tokens > o3_limit
            ):
                effective_max_output_tokens = o3_limit
                logger.debug(
                    "Clamping effective max_output_tokens to %s for %s",
                    effective_max_output_tokens,
                    self.model,
                )

        self._effective_max_output_tokens = effective_max_output_tokens

    def _validate_context_window_size(self) -> None:
        """Validate that the context window is large enough for OpenHands."""
        # Allow override via environment variable
        if os.environ.get(ENV_ALLOW_SHORT_CONTEXT_WINDOWS, "").lower() in (
            "true",
            "1",
            "yes",
        ):
            return

        # Unknown context window - cannot validate
        if self.effective_max_input_tokens is None:
            return

        # Check minimum requirement
        if self.effective_max_input_tokens < MIN_CONTEXT_WINDOW_TOKENS:
            raise LLMContextWindowTooSmallError(
                self.effective_max_input_tokens, MIN_CONTEXT_WINDOW_TOKENS
            )

    def vision_is_active(self) -> bool:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return not self.disable_vision and self._supports_vision()

    def _supports_vision(self) -> bool:
        """Acquire from litellm if model is vision capable.

        Returns:
            bool: True if model is vision capable. Return False if model not
                supported by litellm.
        """
        # litellm.supports_vision currently returns False for 'openai/gpt-...' or 'anthropic/claude-...' (with prefixes)  # noqa: E501
        # but model_info will have the correct value for some reason.
        # we can go with it, but we will need to keep an eye if model_info is correct for Vertex or other providers  # noqa: E501
        # remove when litellm is updated to fix https://github.com/BerriAI/litellm/issues/5608  # noqa: E501
        # Check both the full model name and the name after proxy prefix for vision support  # noqa: E501
        model_for_caps = self._model_name_for_capabilities()
        return (
            supports_vision(model_for_caps)
            or supports_vision(model_for_caps.split("/")[-1])
            or (
                self._model_info is not None
                and self._model_info.get("supports_vision", False)
            )
            or False  # fallback to False if model_info is None
        )

    def is_caching_prompt_active(self) -> bool:
        """Check if prompt caching is supported and enabled for current model.

        Returns:
            boolean: True if prompt caching is supported and enabled for the given
                model.
        """
        if not self.caching_prompt:
            return False
        # We don't need to look up model_info because explicit caching
        # breakpoint support is tracked in the local feature table.
        return (
            self.caching_prompt
            and get_features(self._model_name_for_capabilities()).supports_prompt_cache
        )

    def uses_responses_api(self) -> bool:
        """Whether this model uses the OpenAI Responses API path."""

        # by default, uses = supports
        return get_features(self._model_name_for_capabilities()).supports_responses_api

    @property
    def model_info(self) -> dict | None:
        """Returns the model info dictionary."""
        return self._model_info

    @property
    def effective_max_input_tokens(self) -> int | None:
        """Resolved context window used at runtime.

        ``max_input_tokens`` remains the user-configured value. When it is
        unset, this property reflects the value discovered from model metadata.
        """
        return self.max_input_tokens or self._effective_max_input_tokens

    @property
    def effective_max_output_tokens(self) -> int | None:
        """Resolved output token limit used at runtime.

        ``max_output_tokens`` remains the user-configured value. When it is
        unset, this property reflects provider/model defaults and safety caps.
        """
        return self.max_output_tokens or self._effective_max_output_tokens

    # =========================================================================
    # Utilities preserved from previous class
    # =========================================================================
    def _apply_prompt_caching(self, messages: list[Message]) -> None:
        """Applies caching breakpoints to the messages.

        For Anthropic's prefix caching, we mark specific content blocks:
        1. System message: Mark the first block (static prompt) for caching.
           If there are two blocks (static + dynamic), only the first is marked
           to enable cross-conversation cache sharing.
        2. Last user/tool message: Mark for caching to extend the cache prefix.
        """
        if len(messages) > 0 and messages[0].role == "system":
            sys_content = messages[0].content
            if len(sys_content) >= 2:
                # Two-block structure: static (index 0) + dynamic (index 1)
                # Mark only the static block; ensure dynamic is unmarked
                sys_content[0].cache_prompt = True
                sys_content[1].cache_prompt = False
            elif len(sys_content) == 1:
                # Single block: mark it for caching
                sys_content[0].cache_prompt = True

        # Second breakpoint: mark the last user/tool message so the cached prefix
        # extends every turn. Anthropic-only; Gemini is excluded from
        # PROMPT_CACHE_MODELS because its cache can't extend this way.
        for message in reversed(messages):
            if message.role in ("user", "tool"):
                message.content[
                    -1
                ].cache_prompt = True  # Last item inside the message content
                break

    def _inline_required(self) -> bool:
        """Resolve whether http(s) image URLs must be downloaded and inlined."""
        if self.inline_image_urls is not None:
            return self.inline_image_urls
        return get_features(
            self._model_name_for_capabilities()
        ).requires_inline_image_data

    def _begin_chat_messages(
        self, messages: list[Message]
    ) -> tuple[list[Message], bool]:
        """Deepcopy ``messages`` and apply prompt-caching flags.

        Shared by the sync and async chat-formatting paths. Returns the
        detached message list and the resolved ``vision_enabled`` flag so
        callers can plug in their own (sync or async) inline-image pass
        without duplicating the boilerplate.
        """
        messages = copy.deepcopy(messages)
        if self.is_caching_prompt_active():
            self._apply_prompt_caching(messages)
        return messages, self.vision_is_active()

    def _prepare_chat_messages(self, messages: list[Message]) -> list[Message]:
        """Apply the cache+inline+resize passes, returning detached messages."""
        messages, vision_enabled = self._begin_chat_messages(messages)
        # Inline first (URL → data:), then resize (data: → smaller data:).
        # The resize pass only operates on ``data:image/*`` URLs, so chaining
        # gives us "free" large-image protection for inlined images.
        messages = maybe_inline_image_urls(
            messages,
            inline_required=self._inline_required(),
            vision_enabled=vision_enabled,
        )
        messages = maybe_resize_messages_for_provider(
            messages,
            provider=self._infer_model_info_provider(),
            vision_enabled=vision_enabled,
        )
        return messages

    def _to_chat_dicts(self, messages: list[Message]) -> list[dict]:
        model_features = get_features(self._model_name_for_capabilities())
        cache_enabled = self.is_caching_prompt_active()
        vision_enabled = self.vision_is_active()
        function_calling_enabled = self.native_tool_calling
        force_string_serializer = (
            self.force_string_serializer
            if self.force_string_serializer is not None
            else model_features.force_string_serializer
        )
        send_reasoning_content = model_features.send_reasoning_content
        return [
            message.to_chat_dict(
                cache_enabled=cache_enabled,
                vision_enabled=vision_enabled,
                function_calling_enabled=function_calling_enabled,
                force_string_serializer=force_string_serializer,
                send_reasoning_content=send_reasoning_content,
            )
            for message in messages
        ]

    def format_messages_for_llm(self, messages: list[Message]) -> list[dict]:
        """Formats Message objects for LLM consumption."""
        return self._to_chat_dicts(self._prepare_chat_messages(messages))

    async def aformat_messages_for_llm(self, messages: list[Message]) -> list[dict]:
        """Async variant that runs the blocking inline/resize pass off-loop.

        Keep in sync with ``_prepare_chat_messages``: any new message
        preparation pass added there must also be added here (and in
        ``aformat_messages_for_responses``), because ``await`` cannot be
        used inside the synchronous helper.
        """
        messages, vision_enabled = self._begin_chat_messages(messages)
        messages = await amaybe_inline_image_urls(
            messages,
            inline_required=self._inline_required(),
            vision_enabled=vision_enabled,
        )
        messages = maybe_resize_messages_for_provider(
            messages,
            provider=self._infer_model_info_provider(),
            vision_enabled=vision_enabled,
        )
        return self._to_chat_dicts(messages)

    def _prepare_responses_messages(self, messages: list[Message]) -> list[Message]:
        """Detach messages and optionally strip reasoning items."""
        msgs = copy.deepcopy(messages)

        # Subscription mode (store=false): strip reasoning items from prior
        # assistant turns. The Codex endpoint doesn't persist items, so
        # referencing their IDs in follow-up requests causes a 404.
        if self.is_subscription:
            for m in msgs:
                if m.role == "assistant" and m.responses_reasoning_item is not None:
                    m.responses_reasoning_item = None
        return msgs

    def _build_responses_payload(
        self, msgs: list[Message]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        vision_active = self.vision_is_active()
        instructions: str | None = None
        input_items: list[dict[str, Any]] = []
        system_chunks: list[str] = []

        for m in msgs:
            val = m.to_responses_value(vision_enabled=vision_active)
            if isinstance(val, str):
                s = val.strip()
                if s:
                    if self.is_subscription:
                        system_chunks.append(s)
                    else:
                        instructions = (
                            s
                            if instructions is None
                            else f"{instructions}\n\n---\n\n{s}"
                        )
            elif val:
                input_items.extend(val)

        if self.is_subscription:
            return transform_for_subscription(system_chunks, input_items)
        return instructions, input_items

    def format_messages_for_responses(
        self, messages: list[Message]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Prepare (instructions, input[]) for the OpenAI Responses API.

        - Skips prompt caching flags and string serializer concerns
        - Uses Message.to_responses_value to get either instructions (system)
          or input items (others)
        - Concatenates system instructions into a single instructions string
        - For subscription mode, system prompts are prepended to user content
        - Inlines http(s) image URLs as base64 when the active model requires it
        """
        msgs = self._prepare_responses_messages(messages)
        msgs = maybe_inline_image_urls(
            msgs,
            inline_required=self._inline_required(),
            vision_enabled=self.vision_is_active(),
        )
        return self._build_responses_payload(msgs)

    async def aformat_messages_for_responses(
        self, messages: list[Message]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Async variant that runs the blocking inline pass off-loop.

        Keep in sync with ``format_messages_for_responses``: any new
        message preparation pass added there must also be added here.
        """
        msgs = self._prepare_responses_messages(messages)
        msgs = await amaybe_inline_image_urls(
            msgs,
            inline_required=self._inline_required(),
            vision_enabled=self.vision_is_active(),
        )
        return self._build_responses_payload(msgs)

    def get_token_count(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
    ) -> int:
        logger.debug(
            "Message objects now include serialized tool calls in token counting"
        )
        formatted_messages = self.format_messages_for_llm(messages)
        cc_tools = [
            tool.to_openai_tool(
                add_security_risk_prediction=add_security_risk_prediction,
            )
            for tool in tools or []
        ]
        use_mock_tools = self.should_mock_tool_calls(cc_tools)
        if use_mock_tools:
            tool_call_state: dict[str, Any] = {}
            formatted_messages, _ = self.pre_request_prompt_mock(
                formatted_messages,
                cc_tools,
                tool_call_state,
                include_security_params=add_security_risk_prediction,
            )
            cc_tools = []

        template_count = self._get_chat_template_token_count(
            formatted_messages, cc_tools
        )
        if template_count is not None:
            return template_count

        try:
            return int(
                token_counter(
                    model=self.model,
                    messages=formatted_messages,
                    tools=cc_tools or None,
                    custom_tokenizer=self._tokenizer,
                )
            )
        except Exception as e:
            logger.error(
                f"Error getting token count for model {self.model}\n{e}"
                + (
                    f"\ncustom_tokenizer: {self.custom_tokenizer}"
                    if self.custom_tokenizer
                    else ""
                ),
                exc_info=True,
            )
            return 0

    def _get_chat_template_token_count(
        self,
        formatted_messages: list[dict],
        tools: list[ChatCompletionToolParam],
    ) -> int | None:
        """Count tokens with a tokenizer chat template when one is available.

        LiteLLM's generic token counter estimates OpenAI-style chat/tool overhead.
        Local OpenAI-compatible servers commonly apply the model tokenizer's chat
        template before tokenization, which can differ substantially once tool
        schemas are rendered into the prompt. If a caller configured a tokenizer
        that supports ``apply_chat_template``, prefer that exact rendered prompt
        shape for condenser token checks and fall back to LiteLLM otherwise.
        """
        tokenizer = self._chat_template_tokenizer or self._tokenizer
        if isinstance(tokenizer, dict):
            tokenizer = tokenizer.get("tokenizer")
        if tokenizer is None or not hasattr(tokenizer, "apply_chat_template"):
            return None

        try:
            template_messages = self._messages_for_chat_template(formatted_messages)
            kwargs: dict[str, Any] = {
                "tokenize": True,
                "add_generation_prompt": True,
            }
            if tools:
                kwargs["tools"] = tools
            tokenized = tokenizer.apply_chat_template(template_messages, **kwargs)
            return self._count_tokenized_output(tokenized, tokenizer)
        except Exception:
            logger.warning(
                "Chat-template token counting failed for %d messages and %d tools; "
                "falling back to LiteLLM",
                len(formatted_messages),
                len(tools or []),
                exc_info=True,
            )
            return None

    @staticmethod
    def _count_tokenized_output(tokenized: Any, tokenizer: Any) -> int:
        if isinstance(tokenized, str):
            encoded = tokenizer.encode(tokenized)
            return LLM._count_tokenized_output(encoded, tokenizer)
        if hasattr(tokenized, "shape") and len(tokenized.shape) > 0:
            return int(tokenized.shape[-1])
        if hasattr(tokenized, "ids"):
            return len(tokenized.ids)
        if isinstance(tokenized, dict) and "input_ids" in tokenized:
            return LLM._count_tokenized_output(tokenized["input_ids"], tokenizer)
        get_input_ids = getattr(tokenized, "get", None)
        if callable(get_input_ids):
            input_ids = get_input_ids("input_ids")
            if input_ids is not None:
                return LLM._count_tokenized_output(input_ids, tokenizer)
        encodings = getattr(tokenized, "encodings", None)
        if encodings:
            return LLM._count_tokenized_output(encodings[0], tokenizer)
        if isinstance(tokenized, Sequence):
            if tokenized and hasattr(tokenized[0], "ids"):
                return LLM._count_tokenized_output(tokenized[0], tokenizer)
            if tokenized and isinstance(tokenized[0], Sequence):
                return len(tokenized[0])
            return len(tokenized)
        raise TypeError(f"Unsupported tokenized output: {type(tokenized).__name__}")

    @staticmethod
    def _messages_for_chat_template(messages: list[dict]) -> list[dict]:
        template_messages = copy.deepcopy(messages)
        for message in template_messages:
            content = message.get("content")
            if isinstance(content, list):
                text_parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "text":
                        text_parts = []
                        break
                    text_parts.append(str(block.get("text", "")))
                if text_parts:
                    message["content"] = "".join(text_parts)

            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    continue
                try:
                    parsed_arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed_arguments, dict):
                    function["arguments"] = parsed_arguments
        return template_messages

    @staticmethod
    def _load_chat_template_tokenizer(identifier: str) -> Any | None:
        try:
            transformers = importlib.import_module("transformers")
        except ModuleNotFoundError:
            return None
        except Exception:
            logger.debug("Unable to import transformers", exc_info=True)
            return None

        auto_tokenizer = getattr(transformers, "AutoTokenizer", None)
        if auto_tokenizer is None:
            return None

        try:
            tokenizer = auto_tokenizer.from_pretrained(identifier)
        except Exception:
            logger.debug(
                "Unable to load chat-template tokenizer for %s",
                identifier,
                exc_info=True,
            )
            return None

        if hasattr(tokenizer, "apply_chat_template"):
            return tokenizer
        return None

    @classmethod
    def from_persisted(cls, data: Any, *, context: dict[str, Any] | None = None) -> LLM:
        """Load a persisted LLM profile payload, applying schema migrations."""
        if not isinstance(data, dict):
            return cls.model_validate(data, context=context)

        payload = dict(data)
        version = payload.get("schema_version", 0) or 0
        if type(version) is not int:
            raise ValueError("LLM profile schema_version must be an integer")
        if version > LLM_PROFILE_SCHEMA_VERSION:
            raise ValueError(
                "LLM profile schema_version "
                f"{version} is newer than supported version "
                f"{LLM_PROFILE_SCHEMA_VERSION}"
            )

        payload.pop("schema_version", None)
        payload = canonicalize_openhands_llm_payload(payload)
        llm = cls.model_validate(payload, context=context)
        if llm.auth_type == "subscription":
            from openhands.sdk.llm.auth.openai import (
                create_subscription_llm_from_config,
            )

            return create_subscription_llm_from_config(llm)
        return llm

    def to_persisted(self, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Serialize this LLM for profile persistence."""
        data = self.model_dump(mode="json", exclude_none=True, context=context)
        if data.get("auth_type") == "subscription":
            data.pop("api_key", None)
            data.pop("base_url", None)
            data.pop("extra_headers", None)
        data["schema_version"] = LLM_PROFILE_SCHEMA_VERSION
        return data

    # =========================================================================
    # Serialization helpers
    # =========================================================================
    @classmethod
    def load_from_json(
        cls, json_path: str, *, context: dict[str, Any] | None = None
    ) -> LLM:
        """Load an LLM instance from a JSON file.

        Args:
            json_path: Path to the JSON file containing LLM configuration.
            context: Optional validation context (e.g., ``{"cipher": cipher}``
                for decrypting secrets stored at rest).

        Returns:
            An LLM instance constructed from the JSON configuration.
        """
        with open(json_path) as f:
            data = json.load(f)
        return cls.from_persisted(data, context=context)

    @classmethod
    def load_from_env(cls, prefix: str = "LLM_") -> LLM:
        TRUTHY = {"true", "1", "yes", "on"}

        def _unwrap_type(t: Any) -> Any:
            origin = get_origin(t)
            if origin is None:
                return t
            args = [a for a in get_args(t) if a is not type(None)]
            return args[0] if args else t

        def _cast_value(raw: str, t: Any) -> Any:
            t = _unwrap_type(t)
            if t is SecretStr:
                return SecretStr(raw)
            if t is bool:
                return raw.lower() in TRUTHY
            if t is int:
                try:
                    return int(raw)
                except ValueError:
                    return None
            if t is float:
                try:
                    return float(raw)
                except ValueError:
                    return None
            origin = get_origin(t)
            if (origin in (list, dict, tuple)) or (
                isinstance(t, type) and issubclass(t, BaseModel)
            ):
                try:
                    return json.loads(raw)
                except Exception:
                    pass
            return raw

        data: dict[str, Any] = {}
        fields: dict[str, Any] = {
            name: f.annotation
            for name, f in cls.model_fields.items()
            if not getattr(f, "exclude", False)
        }

        for key, value in os.environ.items():
            if not key.startswith(prefix):
                continue
            field_name = key[len(prefix) :].lower()
            if field_name not in fields:
                continue
            v = _cast_value(value, fields[field_name])
            if v is not None:
                data[field_name] = v
        llm = cls(**data)
        if llm.auth_type == "subscription":
            from openhands.sdk.llm.auth.openai import (
                create_subscription_llm_from_config,
            )

            return create_subscription_llm_from_config(llm)
        return llm

    @classmethod
    def subscription_login(
        cls,
        vendor: SupportedVendor,
        model: str,
        force_login: bool = False,
        open_browser: bool = True,
        auth_method: OpenAIAuthMethod = "browser",
        **llm_kwargs,
    ) -> LLM:
        """Authenticate with a subscription service and return an LLM instance.

        This method provides subscription-based access to LLM models that are
        available through chat subscriptions (e.g., ChatGPT Plus/Pro) rather
        than API credits. It handles credential caching, token refresh, and
        the OAuth login flow.

        Currently supported vendors:
        - "openai": ChatGPT Plus/Pro subscription for Codex models

        Supported OpenAI models:
        - gpt-5.1-codex-max
        - gpt-5.1-codex-mini
        - gpt-5.2
        - gpt-5.2-codex

        Args:
            vendor: The vendor/provider. Currently only "openai" is supported.
            model: The model to use. Must be supported by the vendor's
                subscription service.
            force_login: If True, always perform a fresh login even if valid
                credentials exist.
            open_browser: Whether to automatically open the browser for the
                OAuth login flow.
            auth_method: Login method to use: "browser" or "device_code".
            **llm_kwargs: Additional arguments to pass to the LLM constructor.

        Returns:
            An LLM instance configured for subscription-based access.

        Raises:
            ValueError: If the vendor or model is not supported.
            RuntimeError: If authentication fails.

        Example:
            ```python
            from openhands.sdk import LLM

            # First time: opens browser for OAuth login
            llm = LLM.subscription_login(vendor="openai", model="gpt-5.2-codex")

            # Subsequent calls: reuses cached credentials
            llm = LLM.subscription_login(vendor="openai", model="gpt-5.2-codex")
            ```
        """
        from openhands.sdk.llm.auth.openai import subscription_login

        return subscription_login(
            vendor=vendor,
            model=model,
            force_login=force_login,
            open_browser=open_browser,
            auth_method=auth_method,
            **llm_kwargs,
        )
