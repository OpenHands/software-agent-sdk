"""Hooks service for OpenHands Agent Server.

This module contains the business logic for loading hooks from project and user
locations.

Sources:
- Project hooks: {project_dir}/.openhands/hooks.json
- User hooks: ~/.openhands/hooks.json

The agent-server does not own policy; it only respects request flags.
"""

from __future__ import annotations

from openhands.sdk.hooks import HookConfig, load_project_hooks, load_user_hooks
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def load_hooks(
    *,
    load_project: bool,
    load_user: bool,
    project_dir: str | None,
) -> HookConfig | None:
    hook_configs: list[HookConfig] = []

    if load_project and project_dir:
        try:
            project_hooks = load_project_hooks(project_dir)
        except Exception:
            logger.exception("Failed to load project hooks")
            project_hooks = None

        if project_hooks is not None:
            hook_configs.append(project_hooks)

    if load_user:
        try:
            user_hooks = load_user_hooks()
        except Exception:
            logger.exception("Failed to load user hooks")
            user_hooks = None

        if user_hooks is not None:
            hook_configs.append(user_hooks)

    return HookConfig.merge(hook_configs) if hook_configs else None
