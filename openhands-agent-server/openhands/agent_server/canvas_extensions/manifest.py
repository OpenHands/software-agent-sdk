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
from typing import Final, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from openhands.sdk.extensions.installation.utils import validate_extension_name


# Filename a canvas extension's manifest is loaded from, at its package root.
MANIFEST_FILENAME: Final[str] = "canvas-extension.json"

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


BackendPlatform = Literal["linux-amd64", "linux-arm64"]


class CanvasExtensionBackendArtifact(BaseModel):
    """Immutable backend artifact for one supported platform."""

    path: str = Field(description="Package-relative .tar.gz artifact path")
    sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$", description="Lowercase SHA-256 checksum"
    )

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if (
            not value
            or value.startswith("/")
            or ".." in Path(value).parts
            or not value.endswith(".tar.gz")
        ):
            raise ValueError("artifact path must be a relative .tar.gz path")
        return value


class CanvasExtensionBackendHealth(BaseModel):
    """Loopback HTTP readiness probe for a backend process."""

    path: str = Field(default="/health", pattern=r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    interval_seconds: float = Field(default=0.1, gt=0, le=10)


class CanvasExtensionBackend(BaseModel):
    """Optional trusted backend declaration for schema-1 Canvas Apps."""

    schema_version: Literal[1]
    artifacts: dict[BackendPlatform, CanvasExtensionBackendArtifact] = Field(
        min_length=1
    )
    argv: list[str] = Field(min_length=1)
    health: CanvasExtensionBackendHealth = Field(
        default_factory=CanvasExtensionBackendHealth
    )
    inherit_environment: list[str] = Field(default_factory=list)

    @field_validator("argv")
    @classmethod
    def _validate_argv(cls, value: list[str]) -> list[str]:
        allowed = {"{port}", "{data_dir}", "{artifact_dir}"}
        for argument in value:
            if not argument or "\x00" in argument:
                raise ValueError("backend argv entries must be non-empty")
            placeholders = set(re.findall(r"\{[^{}]+\}", argument))
            if not placeholders.issubset(allowed):
                raise ValueError("backend argv contains an unsupported placeholder")
        return value

    @field_validator("inherit_environment")
    @classmethod
    def _validate_environment(cls, value: list[str]) -> list[str]:
        allowed = {"LANG", "LC_ALL", "LC_CTYPE", "PATH", "TMPDIR", "TZ"}
        if len(value) != len(set(value)):
            raise ValueError("inherit_environment entries must be unique")
        if not set(value).issubset(allowed):
            raise ValueError("inherit_environment contains a disallowed variable")
        return value

    @model_validator(mode="after")
    def _require_artifact_executable(self) -> "CanvasExtensionBackend":
        if "{artifact_dir}" not in self.argv[0]:
            raise ValueError("backend argv executable must be inside {artifact_dir}")
        return self


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
    backend: CanvasExtensionBackend | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Optional explicitly prepared and started backend service",
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported canvas extension schema_version")
        return value

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
