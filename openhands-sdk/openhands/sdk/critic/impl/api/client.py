import copy
from collections.abc import Sequence
from typing import Any, cast

import httpx
from litellm import ChatCompletionToolParam
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    field_validator,
)
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .chat_template import ChatTemplateRenderer


# ============================================================
# Typed API response models
# ============================================================


class UsageTokens(BaseModel):
    prompt_tokens: int | None = None
    total_tokens: int | None = None
    completion_tokens: int | None = None
    prompt_tokens_details: dict | None = None
    model_config = ConfigDict(extra="allow")


class ClassificationItem(BaseModel):
    """One per-label or flat classification result."""

    index: int | None = None
    label: str | None = None
    probs: list[float]
    num_classes: int | None = None
    model_config = ConfigDict(extra="allow")


class ClassificationResponse(BaseModel):
    id: str | None = None
    object: str | None = None
    created: int | None = None
    model: str | None = None
    data: list[ClassificationItem] = Field(default_factory=list)
    usage: UsageTokens | None = None
    model_config = ConfigDict(extra="allow")


class LabelProbMap(BaseModel):
    """Normalized probability map label -> value, with optional ordering."""

    probs: dict[str, float]  # {"label": probability}
    order: list[str] | None = None  # if you requested a specific order
    model_config = ConfigDict(extra="forbid")


# ============================================================
# CriticClient
# ============================================================


class CriticClient(BaseModel):
    """
    Core inference client for the Critic classification service.

    Owns:
      - Configuration (server URL, API key, model, tokenizer, etc.)
      - Label space (for predictions only)
      - Message normalization and chat template formatting
      - Inference via vLLM /classify endpoint

    Does NOT handle:
      - Dataset loading
      - Ground truth extraction
      - Evaluation / metrics
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    # --- connection / model config ---
    server_url: str = Field(
        default="https://all-hands-ai--critic-qwen3-4b-serve.modal.run",
        description="Base URL of the vLLM classification service",
    )
    api_key: str | SecretStr = Field(
        ..., description="API key for authenticating with the vLLM service"
    )
    model_name: str = Field(
        default="critic-qwen3-4b", description="Name of the model to use"
    )
    tokenizer_name: str = Field(
        default="Qwen/Qwen3-4B-Instruct-2507",
        description="HuggingFace tokenizer name for loading chat template",
    )
    pass_tools_definitions: bool = Field(
        default=True, description="Whether to pass tool definitions to the model"
    )
    timeout_seconds: float = Field(
        default=300.0, description="Timeout for requests to the model"
    )
    has_success_label: bool = Field(
        default=True, description="Whether the model predicts success label at index 0"
    )

    # --- runtime fields ---
    _client: httpx.Client = PrivateAttr(default_factory=httpx.Client)
    _template_renderer: ChatTemplateRenderer | None = PrivateAttr(default=None)

    # --- label space ---
    sentiment_labels: tuple[str, ...] = (
        "sentiment_positive",
        "sentiment_neutral",
        "sentiment_negative",
    )
    agent_issue_labels: tuple[str, ...] = (
        "misunderstood_intention",
        "did_not_follow_instruction",
        "insufficient_analysis",
        "insufficient_clarification",
        "improper_tool_use_or_setup",
        "loop_behavior",
        "insufficient_testing",
        "insufficient_debugging",
        "incomplete_implementation",
        "file_management_errors",
        "scope_creep",
        "risky_actions_or_permission",
        "other_agent_issue",
    )
    infra_labels: tuple[str, ...] = (
        "infrastructure_external_issue",
        "infrastructure_agent_caused_issue",
    )
    user_followup_labels: tuple[str, ...] = (
        "clarification_or_restatement",
        "correction",
        "direction_change",
        "vcs_update_requests",
        "progress_or_scope_concern",
        "frustration_or_complaint",
        "removal_or_reversion_request",
        "other_user_issue",
    )
    sentiment_map: dict[str, str] = {
        "Positive": "sentiment_positive",
        "Neutral": "sentiment_neutral",
        "Negative": "sentiment_negative",
    }

    # ---------------------
    # Validation
    # ---------------------
    @field_validator("api_key", mode="before")
    @classmethod
    def _validate_and_convert_api_key(cls, v: str | SecretStr) -> SecretStr:
        """Convert str to SecretStr and validate non-empty."""
        if isinstance(v, SecretStr):
            secret_value = v.get_secret_value()
        else:
            secret_value = v

        if not secret_value or not secret_value.strip():
            raise ValueError("api_key must be non-empty")

        return SecretStr(secret_value) if isinstance(v, str) else v

    # ---------------------
    # Label helpers
    # ---------------------
    @property
    def all_labels(self) -> tuple[str, ...]:
        base_labels = (
            self.sentiment_labels
            + self.agent_issue_labels
            + self.infra_labels
            + self.user_followup_labels
        )
        if self.has_success_label:
            return ("success",) + base_labels
        return base_labels

    # ---------------------
    # Tokenizer / formatting
    # ---------------------
    def _get_template_renderer(self) -> ChatTemplateRenderer:
        """Lazily initialize the chat template renderer."""
        if self._template_renderer is None:
            self._template_renderer = ChatTemplateRenderer(
                tokenizer_name=self.tokenizer_name
            )
        return self._template_renderer

    @staticmethod
    def normalize_messages(messages: Sequence[dict]) -> Sequence[dict]:
        """Ensure messages all have string content and flatten text blocks."""
        out: list[dict] = []
        for msg in messages or []:
            content = msg.get("content", "") or ""
            if isinstance(content, list):
                text_parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                content = "\n".join(text_parts)
            if not isinstance(content, str):
                content = str(content)
            out.append({"role": msg.get("role", ""), "content": content})
        return out

    def apply_chat_template(
        self,
        messages: Sequence[dict],
        tools: Sequence[ChatCompletionToolParam] | None = None,
    ) -> str:
        renderer = self._get_template_renderer()
        msgs = self.normalize_messages(copy.deepcopy(messages))
        # Cast tools to Sequence[dict[str, Any]] for type compatibility
        # ChatCompletionToolParam is a TypedDict which is structurally compatible
        tools_dicts: Sequence[dict[str, Any]] | None = (
            cast(Sequence[dict[str, Any]], tools) if tools is not None else None
        )
        if self.pass_tools_definitions and tools_dicts:
            return renderer.apply_chat_template(
                msgs, tools=tools_dicts, add_generation_prompt=False
            )
        return renderer.apply_chat_template(msgs, add_generation_prompt=False)

    # ---------------------
    # Inference
    # ---------------------
    def classify_trace(
        self,
        messages: Sequence[dict],
        tools: Sequence[ChatCompletionToolParam] | None = None,
    ) -> ClassificationResponse:
        """POST /classify and parse response into ClassificationResponse."""
        formatted = self.apply_chat_template(messages, tools)

        def should_retry(exc: BaseException) -> bool:
            # Retry only on 500 Internal Server Error
            if isinstance(exc, httpx.HTTPStatusError):
                return exc.response.status_code == 500
            return False

        @retry(
            retry=retry_if_exception(should_retry),
            stop=stop_after_attempt(3),  # up to 3 tries
            wait=wait_exponential(
                multiplier=1, min=1, max=8
            ),  # exponential backoff: 1s, 2s, 4s, 8s
            reraise=True,  # re-raise the last exception if all retries fail
        )
        def _post_with_retry():
            api_key_value = (
                self.api_key.get_secret_value()
                if isinstance(self.api_key, SecretStr)
                else self.api_key
            )
            resp = self._client.post(
                f"{self.server_url}/classify",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key_value}",
                },
                json={"model": self.model_name, "input": formatted},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            return resp

        resp = _post_with_retry()
        return ClassificationResponse.model_validate(resp.json())

    # ---------------------
    # Post-processing helpers
    # ---------------------
    def extract_prob_map(self, response: ClassificationResponse) -> LabelProbMap:
        """
        Server format (flat-only, strict):
          response.data == [ ClassificationItem(probs=[p0, p1, ..., pN-1],
                            num_classes=N) ]
        We align probs directly to self.all_labels (same length, same order).
        """
        if not response.data:
            raise ValueError("empty response.data from server")

        item = response.data[0]
        if not item.probs:
            raise ValueError("server returned empty 'probs'")
        if item.num_classes is not None and item.num_classes != len(item.probs):
            raise ValueError(
                f"num_classes ({item.num_classes}) does not match "
                f"len(probs) ({len(item.probs)})"
            )

        probs = [float(x) for x in item.probs]
        if len(probs) != len(self.all_labels):
            raise ValueError(
                f"len(probs) ({len(probs)}) != len(all_labels) "
                f"({len(self.all_labels)}). "
                "Ensure server label space matches client label space."
            )

        mapping = {lbl: probs[i] for i, lbl in enumerate(self.all_labels)}
        return LabelProbMap(probs=mapping, order=list(self.all_labels))

    def predict_labels(self, probs: list[float], threshold: float = 0.5) -> list[int]:
        return [1 if p > threshold else 0 for p in probs]
