from openhands.sdk import LLM, OpenHandsAgentSettings
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.llm.utils.model_features import (
    DEFAULT_CONDENSER_INPUT_RATIO,
    condenser_input_ratio,
)
from openhands.sdk.settings import LLMSummarizingCondenserSettings

QWEN_27B = "custom_openai/Qwen3.8-27B-Q4_K_M.gguf"


def _condenser(
    model: str, budget: int | None, **kwargs: object
) -> LLMSummarizingCondenser:
    llm = LLM(model=model, usage_id="agent", max_input_tokens=budget)
    settings = OpenHandsAgentSettings(
        llm=llm,
        condenser=LLMSummarizingCondenserSettings(enabled=True, **kwargs),
    )
    agent = settings.create_agent()
    assert isinstance(agent.condenser, LLMSummarizingCondenser)
    return agent.condenser


def test_condenser_input_ratio_defaults_for_unknown_models() -> None:
    assert (
        condenser_input_ratio("custom_openai/unknown-model")
        == DEFAULT_CONDENSER_INPUT_RATIO
    )
    assert condenser_input_ratio(None) == DEFAULT_CONDENSER_INPUT_RATIO


def test_condenser_input_ratio_matches_model_rules() -> None:
    assert condenser_input_ratio(QWEN_27B) == 0.685
    # Matching is case-insensitive on the raw model string.
    assert condenser_input_ratio("CUSTOM_OPENAI/QWEN3.8-27B") == 0.685
    # A model without a rule keeps the default.
    assert condenser_input_ratio("qwen3.8") == DEFAULT_CONDENSER_INPUT_RATIO


def test_condenser_token_limit_stays_below_the_model_budget() -> None:
    """A limit equal to the budget can never fire, so derive a fraction."""
    condenser = _condenser(QWEN_27B, 233472)
    assert condenser.max_tokens == int(233472 * 0.685)


def test_condenser_unknown_model_uses_default_ratio() -> None:
    condenser = _condenser("test-model", 65536)
    assert condenser.max_tokens == int(65536 * DEFAULT_CONDENSER_INPUT_RATIO)


def test_condenser_clamps_configured_limit_above_the_budget() -> None:
    condenser = _condenser(QWEN_27B, 233472, max_tokens=235000)
    assert condenser.max_tokens == int(233472 * 0.685)


def test_condenser_respects_configured_limit_below_the_budget() -> None:
    condenser = _condenser(QWEN_27B, 233472, max_tokens=5000)
    assert condenser.max_tokens == 5000


def test_condenser_explicit_none_stays_event_count_only() -> None:
    condenser = _condenser(QWEN_27B, 233472, max_tokens=None)
    assert condenser.max_tokens is None


def test_condenser_without_model_budget_has_no_token_limit() -> None:
    condenser = _condenser("custom_openai/unknown-model", None)
    assert condenser.max_tokens is None
