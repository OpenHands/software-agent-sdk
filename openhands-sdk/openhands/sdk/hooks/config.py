"""Hook configuration loading and management."""

import json
import logging
import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from openhands.sdk.hooks.types import HookEventType


logger = logging.getLogger(__name__)


def _pascal_to_snake(name: str) -> str:
    """Convert PascalCase to snake_case."""
    # Insert underscore before uppercase letters and lowercase everything
    result = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return result


# Valid snake_case field names for hook events.
# This is the single source of truth for hook event types.
HOOK_EVENT_FIELDS: frozenset[str] = frozenset(
    {
        "pre_tool_use",
        "post_tool_use",
        "user_prompt_submit",
        "session_start",
        "session_end",
        "stop",
    }
)


class HookType(str, Enum):
    """Types of hooks that can be executed."""

    COMMAND = "command"  # Shell command executed via subprocess
    PROMPT = "prompt"  # LLM-based evaluation (future)


class HookDefinition(BaseModel):
    """A single hook definition."""

    type: HookType = HookType.COMMAND
    command: str
    timeout: int = 60
    async_: bool = Field(default=False, alias="async")  # 'async' is a reserved keyword

    model_config = {
        "populate_by_name": True,  # Allow both 'async' and 'async_' in input
    }


class HookMatcher(BaseModel):
    """Matches events to hooks based on patterns.

    Supports exact match, wildcard (*), and regex (auto-detected or /pattern/).
    """

    matcher: str = "*"
    hooks: list[HookDefinition] = Field(default_factory=list)

    # Regex metacharacters that indicate a pattern should be treated as regex
    _REGEX_METACHARACTERS = set("|.*+?[]()^$\\")

    def matches(self, tool_name: str | None) -> bool:
        """Check if this matcher matches the given tool name."""
        # Wildcard matches everything
        if self.matcher == "*" or self.matcher == "":
            return True

        if tool_name is None:
            return self.matcher in ("*", "")

        # Check for explicit regex pattern (enclosed in /)
        is_regex = (
            self.matcher.startswith("/")
            and self.matcher.endswith("/")
            and len(self.matcher) > 2
        )
        if is_regex:
            pattern = self.matcher[1:-1]
            try:
                return bool(re.fullmatch(pattern, tool_name))
            except re.error:
                return False

        # Auto-detect regex: if matcher contains metacharacters, treat as regex
        if any(c in self.matcher for c in self._REGEX_METACHARACTERS):
            try:
                return bool(re.fullmatch(self.matcher, tool_name))
            except re.error:
                # Invalid regex, fall through to exact match
                pass

        # Exact match
        return self.matcher == tool_name


class HookConfig(BaseModel):
    """Configuration for all hooks.

    Hooks can be configured either by loading from `.openhands/hooks.json` or
    by directly instantiating with typed fields:

        # Direct instantiation with typed fields (recommended):
        config = HookConfig(
            pre_tool_use=[
                HookMatcher(
                    matcher="terminal",
                    hooks=[HookDefinition(command="block_dangerous.sh")]
                )
            ]
        )

        # Load from JSON file:
        config = HookConfig.load(".openhands/hooks.json")
    """

    model_config = {
        "extra": "forbid",
    }

    pre_tool_use: list[HookMatcher] = Field(
        default_factory=list,
        description="Hooks that run before tool execution",
    )
    post_tool_use: list[HookMatcher] = Field(
        default_factory=list,
        description="Hooks that run after tool execution",
    )
    user_prompt_submit: list[HookMatcher] = Field(
        default_factory=list,
        description="Hooks that run when user submits a prompt",
    )
    session_start: list[HookMatcher] = Field(
        default_factory=list,
        description="Hooks that run when a session starts",
    )
    session_end: list[HookMatcher] = Field(
        default_factory=list,
        description="Hooks that run when a session ends",
    )
    stop: list[HookMatcher] = Field(
        default_factory=list,
        description="Hooks that run when the agent attempts to stop",
    )

    def is_empty(self) -> bool:
        """Check if this config has no hooks configured."""
        return not any(
            [
                self.pre_tool_use,
                self.post_tool_use,
                self.user_prompt_submit,
                self.session_start,
                self.session_end,
                self.stop,
            ]
        )

    @model_validator(mode="before")
    @classmethod
    def _normalize_hooks_input(cls, data: Any) -> Any:
        """Support JSON format with PascalCase keys and 'hooks' wrapper.

        We intentionally continue supporting these formats for interoperability with
        existing integrations (e.g. Claude Code plugin hook files).
        """
        if not isinstance(data, dict):
            return data

        # Unwrap legacy format: {"hooks": {"PreToolUse": [...]}}
        if "hooks" in data:
            if len(data) != 1:
                logger.warning(
                    'HookConfig legacy wrapper format should be {"hooks": {...}}. '
                    "Extra top-level keys will be ignored."
                )
            data = data["hooks"]

        # Convert PascalCase keys to snake_case field names
        normalized: dict[str, Any] = {}
        seen_fields: set[str] = set()

        for key, value in data.items():
            snake_key = _pascal_to_snake(key)
            is_pascal_case = snake_key != key

            if is_pascal_case:
                # Validate that PascalCase key maps to a known field
                if snake_key not in HOOK_EVENT_FIELDS:
                    valid_types = ", ".join(sorted(HOOK_EVENT_FIELDS))
                    raise ValueError(
                        f"Unknown event type '{key}'. Valid types: {valid_types}"
                    )

            # Check for duplicate keys (both PascalCase and snake_case provided)
            if snake_key in seen_fields:
                raise ValueError(
                    f"Duplicate hook event: both '{key}' and its snake_case "
                    f"equivalent '{snake_key}' were provided"
                )
            seen_fields.add(snake_key)
            normalized[snake_key] = value

        # Preserve backwards compatibility without deprecating any supported formats.
        # The legacy 'hooks' wrapper and PascalCase keys are accepted for
        # interoperability and should not emit a deprecation warning.

        return normalized

    @classmethod
    def load(
        cls, path: str | Path | None = None, working_dir: str | Path | None = None
    ) -> "HookConfig":
        """Load config from path or search .openhands/hooks.json locations.

        Args:
            path: Explicit path to hooks.json file. If provided, working_dir is ignored.
            working_dir: Project directory for discovering .openhands/hooks.json.
                Falls back to cwd if not provided.
        """
        if path is None:
            # Search for hooks.json in standard locations
            base_dir = Path(working_dir) if working_dir else Path.cwd()
            search_paths = [
                base_dir / ".openhands" / "hooks.json",
                Path.home() / ".openhands" / "hooks.json",
            ]
            for search_path in search_paths:
                if search_path.exists():
                    path = search_path
                    break

        if path is None:
            return cls()

        path = Path(path)
        if not path.exists():
            return cls()

        with open(path) as f:
            data = json.load(f)
        # Use model_validate which triggers the model_validator
        return cls.model_validate(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HookConfig":
        """Create HookConfig from a dictionary.

        Supports both legacy format with "hooks" wrapper and direct format:
            # Legacy format:
            {"hooks": {"PreToolUse": [...]}}

            # Direct format:
            {"PreToolUse": [...]}
        """
        return cls.model_validate(data)

    def _get_matchers_for_event(self, event_type: HookEventType) -> list[HookMatcher]:
        """Get matchers for an event type."""
        field_name = _pascal_to_snake(event_type.value)
        return getattr(self, field_name, [])

    def get_hooks_for_event(
        self, event_type: HookEventType, tool_name: str | None = None
    ) -> list[HookDefinition]:
        """Get all hooks that should run for an event."""
        matchers = self._get_matchers_for_event(event_type)

        result: list[HookDefinition] = []
        for matcher in matchers:
            if matcher.matches(tool_name):
                result.extend(matcher.hooks)

        return result

    def has_hooks_for_event(self, event_type: HookEventType) -> bool:
        """Check if there are any hooks configured for an event type."""
        matchers = self._get_matchers_for_event(event_type)
        return len(matchers) > 0

    def save(self, path: str | Path) -> None:
        """Save hook configuration to a JSON file using snake_case field names."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(self.model_dump(mode="json", exclude_defaults=True), f, indent=2)

    @classmethod
    def merge(cls, configs: list["HookConfig"]) -> "HookConfig | None":
        """Merge multiple hook configs by concatenating handlers per event type.

        Each hook config may have multiple event types (pre_tool_use,
        post_tool_use, etc.). This method combines all matchers from all
        configs for each event type.

        Args:
            configs: List of HookConfig objects to merge.

        Returns:
            A merged HookConfig with all matchers concatenated, or None if no configs
            or if the result is empty.

        Example:
            >>> config1 = HookConfig(pre_tool_use=[HookMatcher(matcher="*")])
            >>> config2 = HookConfig(pre_tool_use=[HookMatcher(matcher="terminal")])
            >>> merged = HookConfig.merge([config1, config2])
            >>> len(merged.pre_tool_use)  # Both matchers combined
            2
        """
        if not configs:
            return None

        # Collect all matchers by event type using the canonical field list
        collected: dict[str, list] = {field: [] for field in HOOK_EVENT_FIELDS}
        for config in configs:
            for field in HOOK_EVENT_FIELDS:
                collected[field].extend(getattr(config, field))

        merged = cls(**collected)

        # Return None if the merged config is empty
        if merged.is_empty():
            return None

        return merged
