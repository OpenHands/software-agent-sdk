#!/usr/bin/env python3
"""
Single-entry build helper for agent-server images.

- Targets: binary | binary-minimal | source | source-minimal
- Multi-tagging via CUSTOM_TAGS (comma-separated)
- Versioned tags for custom tags: {SDK_VERSION}-{CUSTOM_TAG}
- Branch-scoped cache keys
- CI (push) vs local (load) behavior
- sdist-based builds: Uses `uv build` to create clean build contexts
- One entry: build(opts: BuildOptions)
- Automatically detects sdk_project_root (no manual arg)
- No local artifacts left behind (uses tempfile dirs only)
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import tomllib
from contextlib import chdir
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from openhands.sdk.logger import IN_CI, get_logger, rolling_log_view
from openhands.sdk.workspace import PlatformType, TargetType


logger = get_logger(__name__)

VALID_TARGETS = {"binary", "binary-minimal", "source", "source-minimal"}


# --- helpers ---


def _default_sdk_project_root() -> Path:
    """
    Resolve top-level OpenHands UV workspace root:

    Order:
      1) Walk up from CWD
      2) Walk up from this file location

    Reject anything in site/dist-packages (installed wheels).
    """
    site_markers = ("site-packages", "dist-packages")

    def _is_workspace_root(d: Path) -> bool:
        """Detect if d is the root of the Agent-SDK repo UV workspace."""
        _EXPECTED = (
            "openhands-sdk/pyproject.toml",
            "openhands-tools/pyproject.toml",
            "openhands-workspace/pyproject.toml",
            "openhands-agent-server/pyproject.toml",
        )

        py = d / "pyproject.toml"
        if not py.exists():
            return False
        try:
            cfg = tomllib.loads(py.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        members = (
            cfg.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
            or []
        )
        # Accept either explicit UV members or structural presence of all subprojects
        if members:
            norm = {str(Path(m)) for m in members}
            return {
                "openhands-sdk",
                "openhands-tools",
                "openhands-workspace",
                "openhands-agent-server",
            }.issubset(norm)
        return all((d / p).exists() for p in _EXPECTED)

    def _climb(start: Path) -> Path | None:
        cur = start.resolve()
        if not cur.is_dir():
            cur = cur.parent
        while True:
            if _is_workspace_root(cur):
                return cur
            if cur.parent == cur:
                return None
            cur = cur.parent

    def validate(p: Path, src: str) -> Path:
        if any(s in str(p) for s in site_markers):
            raise RuntimeError(
                f"{src}: points inside site-packages; need the source checkout."
            )
        root = _climb(p) or p
        if not _is_workspace_root(root):
            raise RuntimeError(
                f"{src}: couldn't find the OpenHands UV workspace root "
                f"starting at '{p}'.\n\n"
                "Expected setup (repo root):\n"
                "  pyproject.toml  # has [tool.uv.workspace] with members\n"
                "  openhands-sdk/pyproject.toml\n"
                "  openhands-tools/pyproject.toml\n"
                "  openhands-workspace/pyproject.toml\n"
                "  openhands-agent-server/pyproject.toml\n\n"
                "Fix:\n"
                "  - Run from anywhere inside the repo."
            )
        return root

    if root := _climb(Path.cwd()):
        return validate(root, "CWD discovery")

    try:
        here = Path(__file__).resolve()
        if root := _climb(here):
            return validate(root, "__file__ discovery")
    except NameError:
        pass

    # Final, user-facing guidance
    raise RuntimeError(
        "Could not resolve the OpenHands UV workspace root.\n\n"
        "Expected repo layout:\n"
        "  pyproject.toml  (with [tool.uv.workspace].members "
        "including openhands/* subprojects)\n"
        "  openhands-sdk/pyproject.toml\n"
        "  openhands-tools/pyproject.toml\n"
        "  openhands-workspace/pyproject.toml\n"
        "  openhands-agent-server/pyproject.toml\n\n"
        "Run this from inside the repo."
    )


def _run(
    cmd: list[str],
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    """
    Stream stdout and stderr concurrently into the rolling logger,
    while capturing FULL stdout/stderr.
    Returns CompletedProcess(stdout=<full>, stderr=<full>).
    Raises CalledProcessError with both output and stderr on failure.
    """
    logger.info(f"$ {' '.join(cmd)} (cwd={cwd})")

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,  # keep separate
        bufsize=1,  # line-buffered
    )
    assert proc.stdout is not None and proc.stderr is not None

    out_lines: list[str] = []
    err_lines: list[str] = []

    def pump(stream, sink: list[str], log_fn, prefix: str) -> None:
        for line in stream:
            line = line.rstrip("\n")
            sink.append(line)
            log_fn(f"{prefix}{line}")

    with rolling_log_view(
        logger,
        header="$ " + " ".join(cmd) + (f" (cwd={cwd})" if cwd else ""),
    ):
        t_out = threading.Thread(
            target=pump, args=(proc.stdout, out_lines, logger.info, "[stdout] ")
        )
        t_err = threading.Thread(
            target=pump, args=(proc.stderr, err_lines, logger.warning, "[stderr] ")
        )
        t_out.start()
        t_err.start()
        t_out.join()
        t_err.join()

    rc = proc.wait()
    stdout = ("\n".join(out_lines) + "\n") if out_lines else ""
    stderr = ("\n".join(err_lines) + "\n") if err_lines else ""

    result = subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr=stderr)

    if rc != 0:
        # Include full outputs on failure
        raise subprocess.CalledProcessError(rc, cmd, output=stdout, stderr=stderr)

    return result


def _sanitize_branch(ref: str) -> str:
    ref = re.sub(r"^refs/heads/", "", ref or "unknown")
    return re.sub(r"[^a-zA-Z0-9.-]+", "-", ref).lower()


def _truncate_ident(repo: str, tag: str, budget: int) -> str:
    """
    Truncate repo+tag to fit budget, prioritizing tag preservation.

    Strategy:
    1. If both fit: return both
    2. If tag fits but repo doesn't: truncate repo, keep full tag
    3. If tag doesn't fit: truncate tag, discard repo
    4. If no tag: truncate repo
    """
    tag_suffix = f"_tag_{tag}" if tag else ""
    full_ident = repo + tag_suffix

    if len(full_ident) <= budget:
        return full_ident

    if not tag:
        return repo[:budget]

    if len(tag_suffix) <= budget:
        repo_budget = budget - len(tag_suffix)
        return repo[:repo_budget] + tag_suffix

    return tag_suffix[:budget]


def _base_slug(image: str, max_len: int = 64) -> str:
    """
    If the slug is too long, keep the most identifiable parts:
    - repository name (last path segment)
    - tag (if present)
    Then append a short digest for uniqueness.
    Format preserved with existing separators: '_s_' for '/', '_tag_' for ':'.

    Example:
      'ghcr.io_s_org_s/very-long-repo_tag_v1.2.3-extra'
      ->  'very-long-repo_tag_v1.2.3-<digest>'
    """
    base_slug = image.replace("/", "_s_").replace(":", "_tag_")

    if len(base_slug) <= max_len:
        return base_slug

    digest = hashlib.sha256(base_slug.encode()).hexdigest()[:12]
    suffix = f"-{digest}"

    # Parse components from the slug form
    if "_tag_" in base_slug:
        left, tag = base_slug.rsplit("_tag_", 1)  # Split on last : (rightmost tag)
    else:
        left, tag = base_slug, ""

    parts = left.split("_s_") if left else []
    repo = parts[-1] if parts else left  # last path segment is the repo

    # Fit within budget, reserving space for the digest suffix
    visible_budget = max_len - len(suffix)
    assert visible_budget > 0, (
        f"max_len too small to fit digest suffix with length {len(suffix)}"
    )

    ident = _truncate_ident(repo, tag, visible_budget)
    return ident + suffix


def _git_info() -> tuple[str, str]:
    """
    Get git info (ref, sha) for the current working directory.

    Priority order for SHA:
    1. SDK_SHA - Explicit override (e.g., for submodule builds)
    2. GITHUB_SHA - GitHub Actions environment
    3. git rev-parse HEAD - Local development

    Priority order for REF:
    1. SDK_REF - Explicit override (e.g., for submodule builds)
    2. GITHUB_REF - GitHub Actions environment
    3. git symbolic-ref HEAD - Local development
    """
    sdk_root = _default_sdk_project_root()
    git_sha = os.environ.get("SDK_SHA") or os.environ.get("GITHUB_SHA")
    if not git_sha:
        try:
            git_sha = _run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=str(sdk_root),
            ).stdout.strip()
        except subprocess.CalledProcessError:
            git_sha = "unknown"

    git_ref = os.environ.get("SDK_REF") or os.environ.get("GITHUB_REF")
    if not git_ref:
        try:
            git_ref = _run(
                ["git", "symbolic-ref", "-q", "--short", "HEAD"],
                cwd=str(sdk_root),
            ).stdout.strip()
        except subprocess.CalledProcessError:
            git_ref = "unknown"
    return git_ref, git_sha


def _package_version() -> str:
    """
    Get the semantic version from the openhands-sdk package.
    This is used for versioned tags during releases.
    """
    try:
        from importlib.metadata import version

        return version("openhands-sdk")
    except Exception:
        # If package is not installed, try reading from pyproject.toml
        try:
            sdk_root = _default_sdk_project_root()
            pyproject_path = sdk_root / "openhands-sdk" / "pyproject.toml"
            if pyproject_path.exists():
                cfg = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
                return cfg.get("project", {}).get("version", "unknown")
        except Exception:
            pass
        return "unknown"


_DEFAULT_GIT_REF, _DEFAULT_GIT_SHA = _git_info()
_DEFAULT_PACKAGE_VERSION = _package_version()


class BuildOptions(BaseModel):
    # NOTE: Using Python 3.12 due to PyInstaller+libtmux compatibility issue
    # with Python 3.13. See issue #1886 for details.
    base_image: str = Field(default="nikolaik/python-nodejs:python3.12-nodejs22")
    custom_tags: str = Field(
        default="", description="Comma-separated list of custom tags."
    )
    image: str = Field(default="ghcr.io/openhands/agent-server")
    target: TargetType = Field(default="binary")
    platforms: list[PlatformType] = Field(default=["linux/amd64"])
    push: bool | None = Field(
        default=None, description="None=auto (CI push, local load)"
    )
    arch: str | None = Field(
        default=None,
        description="Architecture suffix (e.g., 'amd64', 'arm64') to append to tags",
    )
    include_base_tag: bool = Field(
        default=True,
        description=(
            "Whether to include the automatically generated base tag "
            "based on git SHA and base image name in all_tags output."
        ),
    )
    include_versioned_tag: bool = Field(
        default=False,
        description=(
            "Whether to include the versioned tag (e.g., v1.0.0_...) in all_tags "
            "output. Should only be True for release builds."
        ),
    )
    git_sha: str = Field(
        default=_DEFAULT_GIT_SHA,
        description="Git commit SHA.We will need it to tag the built image.",
    )
    git_ref: str = Field(default=_DEFAULT_GIT_REF)
    sdk_project_root: Path = Field(
        default_factory=_default_sdk_project_root,
        description="Path to OpenHands SDK root. Auto if None.",
    )
    sdk_version: str = Field(
        default=_DEFAULT_PACKAGE_VERSION,
        description=(
            "SDK package version. "
            "We will need it to tag the built image. "
            "Note this is only used if include_versioned_tag is True "
            "(e.g., at each release)."
        ),
    )

    @property
    def short_sha(self) -> str:
        return self.git_sha[:7] if self.git_sha != "unknown" else "unknown"

    @field_validator("target")
    @classmethod
    def _valid_target(cls, v: str) -> str:
        if v not in VALID_TARGETS:
            raise ValueError(f"target must be one of {sorted(VALID_TARGETS)}")
        return v

    @property
    def custom_tag_list(self) -> list[str]:
        return [t.strip() for t in self.custom_tags.split(",") if t.strip()]

    @property
    def base_image_slug(self) -> str:
        return _base_slug(self.base_image)

    @property
    def versioned_tags(self) -> list[str]:
        """
        Generate simple version tags for each custom tag variant.
        Returns tags like: 1.2.0-python, 1.2.0-java, 1.2.0-golang
        """
        return [f"{self.sdk_version}-{t}" for t in self.custom_tag_list]

    @property
    def base_tag(self) -> str:
        return f"{self.short_sha}-{self.base_image_slug}"

    @property
    def cache_tags(self) -> tuple[str, str]:
        base = f"buildcache-{self.target}-{self.base_image_slug}"
        if self.git_ref in ("main", "refs/heads/main"):
            return f"{base}-main", base
        elif self.git_ref != "unknown":
            return f"{base}-{_sanitize_branch(self.git_ref)}", base
        else:
            return base, base

    @property
    def all_tags(self) -> list[str]:
        tags: list[str] = []
        arch_suffix = f"-{self.arch}" if self.arch else ""

        # Use git commit SHA for commit-based tags
        for t in self.custom_tag_list:
            tags.append(f"{self.image}:{self.short_sha}-{t}{arch_suffix}")

        if self.git_ref in ("main", "refs/heads/main"):
            for t in self.custom_tag_list:
                tags.append(f"{self.image}:main-{t}{arch_suffix}")

        if self.include_base_tag:
            tags.append(f"{self.image}:{self.base_tag}{arch_suffix}")
        if self.include_versioned_tag:
            for versioned_tag in self.versioned_tags:
                tags.append(f"{self.image}:{versioned_tag}{arch_suffix}")

        # Append target suffix for clarity (binary is default, no suffix needed)
        if self.target != "binary":
            tags = [f"{t}-{self.target}" for t in tags]
        return tags


# --- build helpers ---


def _extract_tarball(tarball: Path, dest: Path) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tar, chdir(dest):
        # Pre-validate entries
        for m in tar.getmembers():
            name = m.name.lstrip("./")
            p = Path(name)
            if p.is_absolute() or ".." in p.parts:
                raise RuntimeError(f"Unsafe path in sdist: {m.name}")
        # Safe(-r) extraction: no symlinks/devices
        tar.extractall(path=".", filter="data")


def _make_build_context(sdk_project_root: Path) -> Path:
    dockerfile_path = _get_dockerfile_path(sdk_project_root)
    tmp_root = Path(tempfile.mkdtemp(prefix="agent-build-", dir=None)).resolve()
    sdist_dir = Path(tempfile.mkdtemp(prefix="agent-sdist-", dir=None)).resolve()
    try:
        # sdists = _build_sdists(sdk_project_root, sdist_dir)
        _run(
            ["uv", "build", "--sdist", "--out-dir", str(sdist_dir.resolve())],
            cwd=str(sdk_project_root.resolve()),
        )
        sdists = sorted(sdist_dir.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime)
        logger.info(
            f"[build] Built {len(sdists)} sdists for "
            f"clean context: {', '.join(str(s) for s in sdists)}"
        )
        assert len(sdists) == 1, "Expected exactly one sdist"
        logger.debug(
            f"[build] Extracting sdist {sdists[0]} to clean context {tmp_root}"
        )
        _extract_tarball(sdists[0], tmp_root)

        # assert only one folder created
        entries = list(tmp_root.iterdir())
        assert len(entries) == 1 and entries[0].is_dir(), (
            "Expected single folder in sdist"
        )
        tmp_root = entries[0].resolve()
        # copy Dockerfile into place
        shutil.copy2(dockerfile_path, tmp_root / "Dockerfile")
        logger.debug(f"[build] Clean context ready at {tmp_root}")
        return tmp_root
    except Exception:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(sdist_dir, ignore_errors=True)


def _active_buildx_driver() -> str | None:
    try:
        out = _run(["docker", "buildx", "inspect", "--bootstrap"]).stdout
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Driver:"):
                return s.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def _default_local_cache_dir() -> Path:
    # keep cache outside repo; override with BUILD_CACHE_DIR if wanted
    root = os.environ.get("BUILD_CACHE_DIR")
    if root:
        return Path(root).expanduser().resolve()
    xdg = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(xdg) / "openhands" / "buildx-cache"


def _get_dockerfile_path(sdk_project_root: Path) -> Path:
    dockerfile_path = (
        sdk_project_root
        / "openhands-agent-server"
        / "openhands"
        / "agent_server"
        / "docker"
        / "Dockerfile"
    )
    if not dockerfile_path.exists():
        raise FileNotFoundError(f"Dockerfile not found at {dockerfile_path}")
    return dockerfile_path


# --- single entry point ---


def build(opts: BuildOptions) -> list[str]:
    """Single entry point for building the agent-server image."""
    dockerfile_path = _get_dockerfile_path(opts.sdk_project_root)
    push = opts.push
    if push is None:
        push = IN_CI

    tags = opts.all_tags
    cache_tag, cache_tag_base = opts.cache_tags

    ctx = _make_build_context(opts.sdk_project_root)
    logger.info(f"[build] Clean build context: {ctx}")

    args = [
        "docker",
        "buildx",
        "build",
        "--file",
        str(dockerfile_path),
        "--target",
        opts.target,
        "--build-arg",
        f"BASE_IMAGE={opts.base_image}",
    ]
    if push:
        args += ["--platform", ",".join(opts.platforms), "--push"]
    else:
        args += ["--load"]

    for t in tags:
        args += ["--tag", t]

    # -------- cache strategy --------
    driver = _active_buildx_driver() or "unknown"
    local_cache_dir = _default_local_cache_dir()
    cache_args: list[str] = []

    if push:
        # Remote/CI builds: use registry cache + inline for maximum reuse.
        cache_args += [
            "--cache-from",
            f"type=registry,ref={opts.image}:{cache_tag}",
            "--cache-from",
            f"type=registry,ref={opts.image}:{cache_tag_base}-main",
            "--cache-to",
            f"type=registry,ref={opts.image}:{cache_tag},mode=max",
        ]
        logger.info("[build] Cache: registry (remote/CI) + inline")
    else:
        # Local/dev builds: prefer local dir cache if
        # driver supports it; otherwise inline-only.
        if driver == "docker-container":
            local_cache_dir.mkdir(parents=True, exist_ok=True)
            cache_args += [
                "--cache-from",
                f"type=local,src={str(local_cache_dir)}",
                "--cache-to",
                f"type=local,dest={str(local_cache_dir)},mode=max",
            ]
            logger.info(
                f"[build] Cache: local dir at {local_cache_dir} (driver={driver})"
            )
        else:
            logger.warning(
                f"[build] WARNING: Active buildx driver is '{driver}', "
                "which does not support local dir caching. Fallback to INLINE CACHE\n"
                " Consider running the following commands to set up a "
                "compatible buildx environment:\n"
                "  1. docker buildx create --name openhands-builder "
                "--driver docker-container --use\n"
                "  2. docker buildx inspect --bootstrap\n"
            )
            # docker driver can't export caches; fall back to inline metadata only.
            cache_args += ["--build-arg", "BUILDKIT_INLINE_CACHE=1"]
            logger.info(f"[build] Cache: inline only (driver={driver})")

    args += cache_args + [str(ctx)]

    logger.info(
        f"[build] Building target='{opts.target}' image='{opts.image}' "
        f"custom_tags='{opts.custom_tags}' from base='{opts.base_image}' "
        f"for platforms='{opts.platforms if push else 'local-arch'}'"
    )
    logger.info(
        f"[build] Git ref='{opts.git_ref}' sha='{opts.git_sha}' "
        f"package_version='{opts.sdk_version}'"
    )
    logger.info(f"[build] Cache tag: {cache_tag}")

    try:
        res = _run(args, cwd=str(ctx))
        sys.stdout.write(res.stdout or "")
    except subprocess.CalledProcessError as e:
        logger.error(f"[build] ERROR: Build failed with exit code {e.returncode}")
        logger.error(f"[build] Command: {' '.join(e.cmd)}")
        logger.error(f"[build] Full stdout:\n{e.output}")
        logger.error(f"[build] Full stderr:\n{e.stderr}")
        raise
    finally:
        logger.info(f"[build] Cleaning {ctx}")
        shutil.rmtree(ctx, ignore_errors=True)

    logger.info("[build] Done. Tags:")
    for t in tags:
        logger.info(f" - {t}")
    return tags


# --- CLI shim ---


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v else default


def main(argv: list[str]) -> int:
    # ---- argparse ----
    parser = argparse.ArgumentParser(
        description="Single-entry build helper for agent-server images."
    )
    parser.add_argument(
        "--base-image",
        # NOTE: Using Python 3.12 due to PyInstaller+libtmux compatibility issue
        # with Python 3.13. See issue #1886.
        default=_env("BASE_IMAGE", "nikolaik/python-nodejs:python3.12-nodejs22"),
        help="Base image to use (default from $BASE_IMAGE).",
    )
    parser.add_argument(
        "--custom-tags",
        default=_env("CUSTOM_TAGS", ""),
        help="Comma-separated custom tags (default from $CUSTOM_TAGS).",
    )
    parser.add_argument(
        "--image",
        default=_env("IMAGE", "ghcr.io/openhands/agent-server"),
        help="Image repo/name (default from $IMAGE).",
    )
    parser.add_argument(
        "--target",
        default=_env("TARGET", "binary"),
        choices=sorted(VALID_TARGETS),
        help="Build target (default from $TARGET).",
    )
    parser.add_argument(
        "--platforms",
        default=_env("PLATFORMS", "linux/amd64,linux/arm64"),
        help="Comma-separated platforms (default from $PLATFORMS).",
    )
    parser.add_argument(
        "--arch",
        default=_env("ARCH", ""),
        help=(
            "Architecture suffix for tags (e.g., 'amd64', 'arm64', default from $ARCH)."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--push",
        action="store_true",
        help="Force push via buildx (overrides env).",
    )
    group.add_argument(
        "--load",
        action="store_true",
        help="Force local load (overrides env).",
    )
    parser.add_argument(
        "--sdk-project-root",
        type=Path,
        default=None,
        help="Path to OpenHands SDK root (default: auto-detect).",
    )
    parser.add_argument(
        "--build-ctx-only",
        action="store_true",
        help="Only create the clean build context directory and print its path.",
    )
    parser.add_argument(
        "--versioned-tag",
        action="store_true",
        help=(
            "Include versioned tag (e.g., v1.0.0_...) in output. "
            "Should only be used for release builds."
        ),
    )

    args = parser.parse_args(argv)

    # ---- resolve sdk project root ----
    sdk_project_root = args.sdk_project_root
    if sdk_project_root is None:
        try:
            sdk_project_root = _default_sdk_project_root()
        except Exception as e:
            logger.error(str(e))
            return 1

    # ---- build-ctx-only path ----
    if args.build_ctx_only:
        ctx = _make_build_context(sdk_project_root)
        logger.info(f"[build] Clean build context (kept for debugging): {ctx}")

        # Create BuildOptions to generate tags
        opts = BuildOptions(
            base_image=args.base_image,
            custom_tags=args.custom_tags,
            image=args.image,
            target=args.target,  # type: ignore
            platforms=[p.strip() for p in args.platforms.split(",") if p.strip()],  # type: ignore
            push=None,  # Not relevant for build-ctx-only
            sdk_project_root=sdk_project_root,
            arch=args.arch or None,
            include_versioned_tag=args.versioned_tag,
        )

        # If running in GitHub Actions, write outputs directly to GITHUB_OUTPUT
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a") as fh:
                fh.write(f"build_context={ctx}\n")
                fh.write(f"dockerfile={ctx / 'Dockerfile'}\n")
                fh.write(f"tags_csv={','.join(opts.all_tags)}\n")
                # Only output versioned tags if they're being used
                if opts.include_versioned_tag:
                    fh.write(f"versioned_tags_csv={','.join(opts.versioned_tags)}\n")
                else:
                    fh.write("versioned_tags_csv=\n")
                fh.write(f"base_image_slug={opts.base_image_slug}\n")
            logger.info("[build] Wrote outputs to $GITHUB_OUTPUT")

        # Also print to stdout for debugging/local use
        print(str(ctx))
        return 0

    # ---- push/load resolution (CLI wins over env, else auto) ----
    push: bool | None
    if args.push:
        push = True
    elif args.load:
        push = False
    else:
        push = (
            True
            if os.environ.get("PUSH") == "1"
            else False
            if os.environ.get("LOAD") == "1"
            else None
        )

    # ---- normal build path ----
    opts = BuildOptions(
        base_image=args.base_image,
        custom_tags=args.custom_tags,
        image=args.image,
        target=args.target,  # type: ignore
        platforms=[p.strip() for p in args.platforms.split(",") if p.strip()],  # type: ignore
        push=push,
        sdk_project_root=sdk_project_root,
        arch=args.arch or None,
        include_versioned_tag=args.versioned_tag,
    )
    tags = build(opts)

    # --- expose outputs for GitHub Actions ---
    def _write_gha_outputs(
        image: str,
        short_sha: str,
        versioned_tags: list[str],
        tags_list: list[str],
        include_versioned_tag: bool,
    ) -> None:
        """
        If running in GitHub Actions, append step outputs to $GITHUB_OUTPUT.
        - image: repo/name (no tag)
        - short_sha: 7-char SHA
        - versioned_tags_csv: comma-separated list of versioned tags
          (empty if not enabled)
        - tags: multiline output (one per line)
        - tags_csv: single-line, comma-separated
        """
        out_path = os.environ.get("GITHUB_OUTPUT")
        if not out_path:
            return
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(f"image={image}\n")
            fh.write(f"short_sha={short_sha}\n")
            # Only output versioned tags if they're being used
            if include_versioned_tag:
                fh.write(f"versioned_tags_csv={','.join(versioned_tags)}\n")
            else:
                fh.write("versioned_tags_csv=\n")
            fh.write(f"tags_csv={','.join(tags_list)}\n")
            fh.write("tags<<EOF\n")
            fh.write("\n".join(tags_list) + "\n")
            fh.write("EOF\n")

    _write_gha_outputs(
        opts.image,
        opts.short_sha,
        opts.versioned_tags,
        tags,
        opts.include_versioned_tag,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
