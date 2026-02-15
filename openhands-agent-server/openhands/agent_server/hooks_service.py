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

    def _safe_load(loader, *args, log_message: str):
        try:
            return loader(*args)
        except Exception:
            logger.exception(log_message)
            return None

    if load_project and project_dir:
        project_hooks = _safe_load(
            load_project_hooks,
            project_dir,
            log_message="Failed to load project hooks",
        )
        if project_hooks is not None:
            hook_configs.append(project_hooks)

    if load_user:
        user_hooks = _safe_load(
            load_user_hooks, log_message="Failed to load user hooks"
        )
        if user_hooks is not None:
            hook_configs.append(user_hooks)

    return HookConfig.merge(hook_configs) if hook_configs else None
