"""Canvas Extensions manifest: schema, validation, and entrypoint containment.

A Canvas extension is an installable UI bundle that contributes pages to the
OpenHands Canvas frontend. Extensions are installed and served entirely by
the agent-server (via ``openhands.sdk.extensions.installation``, the same
type-agnostic install-tracking framework Plugins/Skills use); nothing here
is consumed by ``Agent``/``Conversation``.

This module defines the manifest schema (``canvas-extension.json``) and the
two security-critical checks around it:

* Name / contribution-id / page-path validation (syntactic, on the model).
* Entrypoint containment (filesystem-level, once a package root is known) —
  rejects both textual path traversal and symlink escapes.
"""

import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from openhands.sdk.extensions.installation.utils import validate_extension_name


# Absolute, kebab-case, multi-segment UI route, e.g. "/dashboard/settings".
_PAGE_PATH_PATTERN: re.Pattern[str] = re.compile(
    r"^/[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$"
)


class CanvasExtensionPage(BaseModel):
    """A single page contributed to the Canvas UI by an extension."""

    id: str = Field(description="Unique contribution id within the extension")
    title: str = Field(description="Page title shown in Canvas navigation")
    path: str = Field(description="Route the page is mounted at, e.g. '/dashboard'")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        try:
            validate_extension_name(v)
        except ValueError as e:
            raise ValueError(
                f"Invalid contribution id. Expected kebab-case, got {v!r}."
            ) from e
        return v

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        if not _PAGE_PATH_PATTERN.fullmatch(v):
            raise ValueError(
                "Invalid page path. Expected an absolute kebab-case route "
                f"(e.g. '/dashboard'), got {v!r}."
            )
        return v


class CanvasExtensionContributes(BaseModel):
    """Contributions an extension makes to the Canvas UI."""

    pages: list[CanvasExtensionPage] = Field(
        default_factory=list, description="Pages contributed to Canvas navigation"
    )

    @field_validator("pages")
    @classmethod
    def _validate_unique_pages(
        cls, v: list[CanvasExtensionPage]
    ) -> list[CanvasExtensionPage]:
        seen_ids: set[str] = set()
        seen_paths: set[str] = set()
        for page in v:
            if page.id in seen_ids:
                raise ValueError(f"Duplicate page contribution id: {page.id!r}")
            if page.path in seen_paths:
                raise ValueError(f"Duplicate page path: {page.path!r}")
            seen_ids.add(page.id)
            seen_paths.add(page.path)
        return v


class CanvasExtensionManifest(BaseModel):
    """Canvas extension manifest (``canvas-extension.json``)."""

    schema_version: int = Field(description="Manifest schema version")
    name: str = Field(description="Extension name (kebab-case)")
    display_name: str = Field(description="Human-readable extension name")
    version: str = Field(description="Extension version")
    description: str = Field(default="", description="Extension description")
    entrypoint: str = Field(
        description=(
            "Path, relative to the extension package root, to the bundle entry file"
        )
    )
    contributes: CanvasExtensionContributes = Field(
        default_factory=CanvasExtensionContributes,
        description="Contributions this extension makes to the Canvas UI",
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        validate_extension_name(v)
        return v

    @field_validator("entrypoint")
    @classmethod
    def _validate_entrypoint(cls, v: str) -> str:
        """Reject textual traversal/absolute paths.

        Syntactic only — see :func:`resolve_entrypoint` for the real,
        symlink-aware containment check against the installed package root.
        """
        if not v:
            raise ValueError("entrypoint must not be empty")
        if v.startswith("/"):
            raise ValueError("entrypoint must be relative, not absolute")
        if ".." in Path(v).parts:
            raise ValueError(
                "entrypoint cannot contain '..' (parent directory traversal)"
            )
        return v


def resolve_entrypoint(manifest: CanvasExtensionManifest, package_root: Path) -> Path:
    """Resolve ``manifest.entrypoint`` against ``package_root``, safely.

    Field-level validation on ``entrypoint`` only rejects textual traversal
    (``..``) and absolute paths. It cannot catch a symlink inside the
    package that resolves outside of it. This performs the real
    filesystem-level containment check (resolving symlinks) and must be
    called both when an extension is installed and again immediately
    before its entrypoint is read or served over HTTP.

    Args:
        manifest: A validated manifest.
        package_root: The extension's installed package root directory.

    Returns:
        The resolved, contained entrypoint path.

    Raises:
        ValueError: If the resolved entrypoint escapes ``package_root``, or
            does not resolve to a regular file within it (covers ``.``,
            a directory, a dangling symlink, and symlink cycles — none of
            which ``is_relative_to`` alone rejects).
    """
    root = package_root.resolve()
    candidate = (root / manifest.entrypoint).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(
            f"entrypoint {manifest.entrypoint!r} resolves outside the "
            "extension package root"
        )
    if not candidate.is_file():
        raise ValueError(
            f"entrypoint {manifest.entrypoint!r} does not resolve to a file "
            "in the extension package"
        )
    return candidate
