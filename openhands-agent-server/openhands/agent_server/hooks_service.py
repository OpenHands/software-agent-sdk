"""Hooks service for OpenHands Agent Server.

This module contains the business logic for loading hooks from the workspace,
keeping the router clean and focused on HTTP concerns.

Hook Sources:
- Project hooks: {workspace}/.openhands/hooks.json
- User hooks: ~/.openhands/hooks.json (or $OH_PERSISTENCE_DIR/hooks.json)
"""

from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.path import get_user_persistence_dir


logger = get_logger(__name__)

HookSource = Literal["project", "user"]


class HooksSourceError(BaseModel):
    """Error encountered when a hooks file exists on disk but cannot be loaded."""

    source: HookSource = Field(
        description="Hook source that failed to load ('project' or 'user')"
    )
    path: str = Field(description="Path to the hooks file that failed to load")
    message: str = Field(description="Error message describing why loading failed")


def load_hooks_with_errors(
    project_dir: str | None = None,
    load_project_hooks: bool = True,
    load_user_hooks: bool = True,
) -> tuple[HookConfig | None, list[HooksSourceError]]:
    """Load project and user hooks, returning both merged config and source errors.

    Absent or empty hooks files are treated as "no hooks" without error.
    Files that exist on disk but fail to parse or validate are logged with
    ``logger.error`` and recorded in the returned ``errors`` list while valid
    sources are still merged.
    """
    sources: list[tuple[HookSource, Path]] = []
    if load_project_hooks:
        if project_dir:
            sources.append(("project", Path(project_dir) / ".openhands" / "hooks.json"))
        else:
            logger.debug("No project_dir provided, skipping project hooks")
    if load_user_hooks:
        user_path = get_user_persistence_dir() / "hooks.json"
        if not any(_same_file(user_path, p) for _, p in sources):
            sources.append(("user", user_path))

    configs: list[HookConfig] = []
    errors: list[HooksSourceError] = []
    for source, hooks_path in sources:
        config, err = _load_hooks_file_with_error(source, hooks_path)
        if config is not None:
            configs.append(config)
        if err is not None:
            errors.append(err)

    return HookConfig.merge(configs), errors


def load_hooks(
    project_dir: str | None = None,
    load_project_hooks: bool = True,
    load_user_hooks: bool = True,
) -> HookConfig | None:
    """Load project and user hooks, merged.

    Unlike ``HookConfig.load()``, which reads the first hooks file it finds,
    this reads both sources and merges them. Project hooks come first, so
    their handlers run before user handlers for the same event. A project dir
    whose ``.openhands/hooks.json`` is the user hooks file is read once.

    Args:
        project_dir: Workspace directory path for project hooks.
        load_project_hooks: Load ``{project_dir}/.openhands/hooks.json``.
        load_user_hooks: Load the user hooks file, ``~/.openhands/hooks.json``.

    Returns:
        The merged HookConfig, or None if no source has hooks.
    """
    config, _ = load_hooks_with_errors(
        project_dir=project_dir,
        load_project_hooks=load_project_hooks,
        load_user_hooks=load_user_hooks,
    )
    return config


def _same_file(a: Path, b: Path) -> bool:
    # Python 3.12 raises RuntimeError on a symlink loop; 3.13 raises OSError.
    with suppress(OSError, RuntimeError):
        return a.resolve() == b.resolve()
    return False


def _load_hooks_file_with_error(
    source: HookSource, hooks_path: Path
) -> tuple[HookConfig | None, HooksSourceError | None]:
    try:
        if not hooks_path.exists():
            logger.debug(f"No hooks.json found at {hooks_path}")
            return None, None

        hook_config = HookConfig.load(path=hooks_path)

        if hook_config.is_empty():
            logger.debug(f"hooks.json at {hooks_path} is empty")
            return None, None

        logger.info(f"Loaded hooks from {hooks_path}")
        return hook_config, None

    except Exception as e:
        logger.error(f"Failed to load hooks from {hooks_path}: {e}", exc_info=True)
        return None, HooksSourceError(
            source=source,
            path=str(hooks_path),
            message=str(e),
        )


def _load_hooks_file(hooks_path: Path) -> HookConfig | None:
    config, _ = _load_hooks_file_with_error("project", hooks_path)
    return config
