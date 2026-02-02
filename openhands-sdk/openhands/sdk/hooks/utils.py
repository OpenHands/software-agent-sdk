"""Utility functions for hook loading and management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.hooks.types import HookEventType


if TYPE_CHECKING:
    from openhands.sdk.hooks.config import HookConfig

logger = logging.getLogger(__name__)


# Mapping of script filenames to hook event types.
# Scripts are named by event type (e.g., stop.sh).
HOOK_SCRIPT_MAPPING: dict[str, HookEventType] = {
    "stop.sh": HookEventType.STOP,
    "pre_tool_use.sh": HookEventType.PRE_TOOL_USE,
    "post_tool_use.sh": HookEventType.POST_TOOL_USE,
    "user_prompt_submit.sh": HookEventType.USER_PROMPT_SUBMIT,
    "session_start.sh": HookEventType.SESSION_START,
    "session_end.sh": HookEventType.SESSION_END,
}

# Default timeout for hook scripts (10 minutes)
DEFAULT_HOOK_SCRIPT_TIMEOUT = 600


def discover_hook_scripts(openhands_dir: Path) -> dict[HookEventType, list[Path]]:
    """Discover hook scripts in the .openhands directory.

    Searches for executable shell scripts that match known hook event types.
    Scripts can be placed directly in .openhands/ or in .openhands/hooks/.

    Args:
        openhands_dir: Path to the .openhands directory.

    Returns:
        Dictionary mapping HookEventType to list of script paths found.
    """
    discovered: dict[HookEventType, list[Path]] = {}

    if not openhands_dir.exists() or not openhands_dir.is_dir():
        return discovered

    # Search locations: .openhands/ and .openhands/hooks/
    search_dirs = [openhands_dir]
    hooks_subdir = openhands_dir / "hooks"
    if hooks_subdir.exists() and hooks_subdir.is_dir():
        search_dirs.append(hooks_subdir)

    for search_dir in search_dirs:
        for script_name, event_type in HOOK_SCRIPT_MAPPING.items():
            script_path = search_dir / script_name
            if script_path.exists() and script_path.is_file():
                if event_type not in discovered:
                    discovered[event_type] = []
                # Avoid duplicates (same script found in multiple locations)
                if script_path not in discovered[event_type]:
                    discovered[event_type].append(script_path)
                    logger.debug(
                        f"Discovered hook script: {script_path} -> {event_type.value}"
                    )

    return discovered


def load_project_hooks(work_dir: str | Path) -> HookConfig:
    """Load hooks from project-specific files in the .openhands directory.

    Discovers hook scripts in {work_dir}/.openhands/ and creates a HookConfig
    with the appropriate hook definitions. This is similar to load_project_skills
    but for hooks.

    Supported script locations:
    - {work_dir}/.openhands/{event_type}.sh (e.g., stop.sh, pre_tool_use.sh)
    - {work_dir}/.openhands/hooks/{event_type}.sh

    Args:
        work_dir: Path to the project/working directory.

    Returns:
        HookConfig with hooks discovered from script files.
        Returns empty HookConfig if no hooks found.
    """
    # Import here to avoid circular dependency
    from openhands.sdk.hooks.config import (
        HookConfig,
        HookDefinition,
        HookMatcher,
        HookType,
        _pascal_to_snake,
    )

    if isinstance(work_dir, str):
        work_dir = Path(work_dir)

    openhands_dir = work_dir / ".openhands"
    discovered_scripts = discover_hook_scripts(openhands_dir)

    if not discovered_scripts:
        logger.debug(f"No hook scripts found in {openhands_dir}")
        return HookConfig()

    # Build hook config from discovered scripts
    hook_data: dict[str, list[HookMatcher]] = {}

    for event_type, script_paths in discovered_scripts.items():
        field_name = _pascal_to_snake(event_type.value)
        matchers: list[HookMatcher] = []

        for script_path in script_paths:
            # Use relative path from work_dir for the command
            relative_path = script_path.relative_to(work_dir)
            # Log failures to stderr but don't block the event
            command = (
                f'bash {relative_path} || {{ echo "Hook script {relative_path} '
                f'failed with exit code $?" >&2; true; }}'
            )

            matchers.append(
                HookMatcher(
                    matcher="*",
                    hooks=[
                        HookDefinition(
                            type=HookType.COMMAND,
                            command=command,
                            timeout=DEFAULT_HOOK_SCRIPT_TIMEOUT,
                        )
                    ],
                )
            )

        if matchers:
            hook_data[field_name] = matchers

    num_scripts = sum(len(m) for m in hook_data.values())
    logger.debug(f"Loaded {num_scripts} hook scripts from {openhands_dir}")
    return HookConfig(**hook_data)
