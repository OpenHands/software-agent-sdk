"""Utilities for detecting model families.

These helpers allow prompts and other systems to tailor behavior for specific
LLM providers while keeping naming heuristics centralized.
"""

from __future__ import annotations


_MODEL_FAMILY_PATTERNS: dict[str, tuple[str, ...]] = {
    "openai_gpt": (
        "gpt-",
        "o1",
        "o3",
        "o4",
    ),
    "anthropic_claude": ("claude",),
    "google_gemini": ("gemini",),
    "meta_llama": ("llama",),
    "mistral": ("mistral",),
    "deepseek": ("deepseek",),
    "alibaba_qwen": ("qwen",),
}


def _normalize(name: str | None) -> str:
    return (name or "").strip().lower()


def _match_family(model_name: str) -> str | None:
    normalized = _normalize(model_name)
    if not normalized:
        return None

    for family, patterns in _MODEL_FAMILY_PATTERNS.items():
        if any(pattern in normalized for pattern in patterns):
            return family
    return None


def get_model_family(model_name: str, canonical_name: str | None = None) -> str | None:
    """Return the detected model family for the given identifiers.

    Args:
        model_name: Raw model identifier configured on the LLM.
        canonical_name: Optional canonical model identifier supplied via
            ``LLM.model_canonical_name``.

    Returns:
        A lowercase slug describing the detected family (e.g., ``"openai_gpt"``)
        or ``None`` if no known family matches.
    """

    family = _match_family(model_name)
    if family is not None:
        return family

    if canonical_name:
        return _match_family(canonical_name)
    return None


__all__ = ["get_model_family"]
