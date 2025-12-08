"""Utilities for detecting model families and variants.

These helpers allow prompts and other systems to tailor behavior for specific
LLM providers while keeping naming heuristics centralized.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPromptSpec:
    """Detected prompt metadata for a given model configuration."""

    family: str | None
    variant: str | None


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

# Ordered heuristics to pick the most specific variant available for a family.
_MODEL_VARIANT_PATTERNS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "openai_gpt": (
        ("gpt-5-codex", ("gpt-5-codex",)),
        ("gpt-5", ("gpt-5",)),
    ),
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


def _match_variant(
    family: str,
    model_name: str,
    canonical_name: str | None = None,
) -> str | None:
    patterns = _MODEL_VARIANT_PATTERNS.get(family)
    if not patterns:
        return None

    candidates: list[str] = []
    normalized_model = _normalize(model_name)
    if normalized_model:
        candidates.append(normalized_model)
    normalized_canonical = _normalize(canonical_name)
    if normalized_canonical and normalized_canonical not in candidates:
        candidates.append(normalized_canonical)

    for variant, substrings in patterns:
        for candidate in candidates:
            if any(pattern in candidate for pattern in substrings):
                return variant
    return None


def get_model_prompt_spec(
    model_name: str,
    canonical_name: str | None = None,
) -> ModelPromptSpec:
    """Return family and variant prompt metadata for the given identifiers."""

    family = _match_family(model_name)
    if family is None and canonical_name:
        family = _match_family(canonical_name)

    variant = None
    if family is not None:
        variant = _match_variant(family, model_name, canonical_name)

    return ModelPromptSpec(family=family, variant=variant)


__all__ = ["ModelPromptSpec", "get_model_prompt_spec"]
