"""Critic taxonomy - mapping of features to categories for visualization."""

import math
from typing import Any


# Feature to category mapping
FEATURE_CATEGORIES: dict[str, str] = {
    # General Context & Task Classification
    "user_goal_summary": "general_context",
    "overall_sentiment": "general_context",
    # Agent Behavioral Issues
    "misunderstood_intention": "agent_behavioral_issues",
    "did_not_follow_instruction": "agent_behavioral_issues",
    "insufficient_analysis": "agent_behavioral_issues",
    "insufficient_clarification": "agent_behavioral_issues",
    "improper_tool_use_or_setup": "agent_behavioral_issues",
    "loop_behavior": "agent_behavioral_issues",
    "insufficient_testing": "agent_behavioral_issues",
    "insufficient_debugging": "agent_behavioral_issues",
    "incomplete_implementation": "agent_behavioral_issues",
    "file_management_errors": "agent_behavioral_issues",
    "scope_creep": "agent_behavioral_issues",
    "risky_actions_or_permission": "agent_behavioral_issues",
    "other_agent_issue": "agent_behavioral_issues",
    # User Follow-Up Patterns
    "follow_up_timing": "user_followup_patterns",
    "clarification_or_restatement": "user_followup_patterns",
    "correction": "user_followup_patterns",
    "direction_change": "user_followup_patterns",
    "vcs_update_requests": "user_followup_patterns",
    "progress_or_scope_concern": "user_followup_patterns",
    "frustration_or_complaint": "user_followup_patterns",
    "removal_or_reversion_request": "user_followup_patterns",
    "other_user_issue": "user_followup_patterns",
    # Infrastructure Issues
    "infrastructure_external_issue": "infrastructure_issues",
    "infrastructure_agent_caused_issue": "infrastructure_issues",
}

# Category display names for visualization
CATEGORY_DISPLAY_NAMES: dict[str, str] = {
    "general_context": "General Context",
    "agent_behavioral_issues": "Detected Agent Behavioral Issues",
    "user_followup_patterns": "Predicted User Follow-Up Patterns",
    "infrastructure_issues": "Detected Infrastructure Issues",
}


def get_category(feature_name: str) -> str | None:
    """Get the category for a feature.

    Args:
        feature_name: Name of the feature

    Returns:
        Category name or None if not found
    """
    return FEATURE_CATEGORIES.get(feature_name)


def _softmax_normalize(probs: dict[str, float]) -> dict[str, float]:
    """Apply softmax normalization to convert logits to probabilities.

    Args:
        probs: Dictionary of names to raw probability/logit values

    Returns:
        Dictionary with softmax-normalized probabilities that sum to 1.0
    """
    if not probs:
        return {}

    values = list(probs.values())
    exp_values = [math.exp(v) for v in values]
    exp_sum = sum(exp_values)
    normalized = [exp_v / exp_sum for exp_v in exp_values]

    return dict(zip(probs.keys(), normalized))


def categorize_features(
    probs_dict: dict[str, float],
    display_threshold: float = 0.2,
) -> dict[str, Any]:
    """Categorize features from probability dictionary into taxonomy groups.

    This function takes raw probability outputs from the critic model and
    organizes them into categories ready for visualization.

    Args:
        probs_dict: Dictionary of feature names to probability values
        display_threshold: Minimum probability to include a feature (default: 0.2)

    Returns:
        Dictionary with categorized features ready for visualization:
        {
            "sentiment": {
                "predicted": "Neutral",
                "probability": 0.77,
                "all": {"positive": 0.10, "neutral": 0.77, "negative": 0.13}
            },
            "agent_behavioral_issues": [
                {"name": "loop_behavior", "display_name": "Loop Behavior",
                 "probability": 0.85},
                ...
            ],
            "user_followup_patterns": [...],
            "infrastructure_issues": [...],
            "other": [...]
        }
    """
    result: dict[str, Any] = {
        "sentiment": None,
        "agent_behavioral_issues": [],
        "user_followup_patterns": [],
        "infrastructure_issues": [],
        "other": [],
    }

    # Extract sentiment features and apply softmax normalization
    raw_sentiment_probs = {}
    for feature_name, prob in probs_dict.items():
        if feature_name.startswith("sentiment_"):
            short_name = feature_name.replace("sentiment_", "")
            raw_sentiment_probs[short_name] = prob

    if raw_sentiment_probs:
        # Apply softmax normalization to convert logits to probabilities
        sentiment_probs = _softmax_normalize(raw_sentiment_probs)
        max_sentiment = max(sentiment_probs.items(), key=lambda x: x[1])
        result["sentiment"] = {
            "predicted": max_sentiment[0].capitalize(),
            "probability": max_sentiment[1],
            "all": sentiment_probs,
        }

    # Categorize other features
    for feature_name, prob in probs_dict.items():
        # Skip sentiment features (already processed)
        if feature_name.startswith("sentiment_"):
            continue

        # Skip 'success' as it's redundant with the score
        if feature_name == "success":
            continue

        # Skip features below threshold
        if prob < display_threshold:
            continue

        category = FEATURE_CATEGORIES.get(feature_name)
        feature_entry = {
            "name": feature_name,
            "display_name": feature_name.replace("_", " ").title(),
            "probability": prob,
        }

        if category == "general_context":
            # Skip general context features for now
            continue
        elif category == "agent_behavioral_issues":
            result["agent_behavioral_issues"].append(feature_entry)
        elif category == "user_followup_patterns":
            result["user_followup_patterns"].append(feature_entry)
        elif category == "infrastructure_issues":
            result["infrastructure_issues"].append(feature_entry)
        else:
            result["other"].append(feature_entry)

    # Sort each category by probability (descending)
    for key in [
        "agent_behavioral_issues",
        "user_followup_patterns",
        "infrastructure_issues",
        "other",
    ]:
        result[key] = sorted(result[key], key=lambda x: x["probability"], reverse=True)

    return result
