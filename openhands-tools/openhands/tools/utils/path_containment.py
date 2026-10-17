"""Opt-in containment of file-tool paths to the workspace.

File tools resolve agent-supplied paths on the host filesystem. By default they
may touch any path the process can access, which is the documented behavior of
``LocalWorkspace``. When a tool is created with ``restrict_to_workspace=True``,
it uses :class:`PathContainment` to reject paths that resolve (after
normalizing ``..`` and following symlinks) outside the workspace root or any
explicitly allowed extra roots.

This is a guard rail for file tools, not a sandbox: the terminal tool can still
reach any path. Use a container-backed workspace (e.g. ``DockerWorkspace``) for
real isolation.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path


class PathOutsideWorkspaceError(ValueError):
    """Raised when a path resolves outside every allowed root."""

    def __init__(self, path: str | Path, roots: Sequence[Path], reason: str = ""):
        self.path = str(path)
        self.roots = tuple(roots)
        roots_text = ", ".join(str(r) for r in self.roots)
        detail = f" ({reason})" if reason else ""
        super().__init__(
            f"Access to '{self.path}' was denied: the path is outside the "
            f"workspace{detail}. This tool is restricted to: {roots_text}. "
            "Use a path inside the workspace instead."
        )


def resolve_path(path: str | Path, base: str | Path) -> Path:
    """Resolve ``path`` against ``base``: make it absolute, collapse ``..``
    and follow symlinks. Nonexistent trailing components are allowed (for
    file creation).

    Raises:
        OSError / RuntimeError: if resolution fails (e.g. a symlink loop).
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(base) / candidate
    return candidate.resolve()


def is_within_roots(path: Path, roots: Iterable[Path]) -> bool:
    """True if the already-resolved ``path`` equals or is inside one of
    ``roots`` (also resolved). Compares whole path components, so ``/tmp/ws2``
    is not inside ``/tmp/ws``, and uses ``os.path.normcase`` so the check
    matches the platform's case rules."""
    target = os.path.normcase(str(path))
    for root in roots:
        root_s = os.path.normcase(str(root))
        try:
            if os.path.commonpath([target, root_s]) == root_s:
                return True
        except ValueError:  # e.g. different drives on Windows
            continue
    return False


@dataclass(frozen=True)
class PathContainment:
    """Resolved set of roots a restricted tool may access."""

    roots: tuple[Path, ...]

    @classmethod
    def create(
        cls,
        workspace_root: str | Path,
        allowed_roots: Sequence[str | Path] | None = None,
    ) -> PathContainment:
        roots = [Path(workspace_root).resolve()]
        for extra in allowed_roots or ():
            extra_path = Path(extra)
            if not extra_path.is_absolute():
                raise ValueError(
                    f"allowed_roots entries must be absolute paths, got: {extra}"
                )
            roots.append(extra_path.resolve())
        return cls(roots=tuple(dict.fromkeys(roots)))

    @classmethod
    def from_settings(
        cls,
        workspace_root: str | Path,
        restrict_to_workspace: bool,
        allowed_roots: Sequence[str | Path] | None = None,
    ) -> PathContainment | None:
        """Return a containment policy, or ``None`` when restriction is off."""
        if allowed_roots and not restrict_to_workspace:
            raise ValueError("allowed_roots requires restrict_to_workspace=True")
        if not restrict_to_workspace:
            return None
        return cls.create(workspace_root, allowed_roots)

    @property
    def workspace_root(self) -> Path:
        return self.roots[0]

    def contains(self, resolved_path: Path) -> bool:
        return is_within_roots(resolved_path, self.roots)

    def check(self, path: str | Path, base: str | Path | None = None) -> Path:
        """Resolve ``path`` (relative to ``base`` or the workspace root) and
        return it if allowed; otherwise raise :class:`PathOutsideWorkspaceError`.
        """
        try:
            resolved = resolve_path(path, base or self.workspace_root)
        except (OSError, RuntimeError) as e:
            raise PathOutsideWorkspaceError(
                path, self.roots, f"it could not be resolved: {e}"
            ) from e
        if not self.contains(resolved):
            reason = (
                f"it resolves to '{resolved}'" if str(resolved) != str(path) else ""
            )
            raise PathOutsideWorkspaceError(path, self.roots, reason)
        return resolved
