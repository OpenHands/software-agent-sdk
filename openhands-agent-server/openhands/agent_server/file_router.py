import asyncio
import fnmatch
import io
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import IO, Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from openhands.agent_server.config import get_default_config
from openhands.agent_server.models import Success
from openhands.agent_server.server_details_router import update_last_execution_time
from openhands.sdk.git.exceptions import GitCommandError, GitRepositoryError
from openhands.sdk.git.utils import (
    GIT_EMPTY_TREE_HASH,
    get_valid_ref,
    run_git_command,
    run_git_subprocess,
    validate_git_repository,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.redact import redact_url_credentials


class SubdirectoryEntry(BaseModel):
    name: str
    path: str


class SubdirectoryPage(BaseModel):
    items: list[SubdirectoryEntry]
    next_page_id: str | None = None


class FileBrowserEntry(BaseModel):
    label: str
    path: str


class HomeResponse(BaseModel):
    home: str
    favorites: list[FileBrowserEntry] = []
    locations: list[FileBrowserEntry] = []


logger = get_logger(__name__)
file_router = APIRouter(prefix="/file", tags=["Files"])


async def _upload_file(path: str, file: UploadFile) -> Success:
    """Internal helper to upload a file to the workspace."""
    update_last_execution_time()
    logger.info(f"Uploading file: {path}")
    try:
        target_path = Path(path)
        if not target_path.is_absolute():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path must be absolute",
            )

        # Ensure target directory exists
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Stream the file to disk to avoid memory issues with large files.
        # Offload writes to a worker thread so slow storage (NFS, FUSE,
        # encrypted FS) cannot starve the event loop for the upload's
        # duration.
        with open(target_path, "wb") as f:
            while chunk := await file.read(8192):  # Read in 8KB chunks
                await asyncio.to_thread(f.write, chunk)

        logger.info(f"Uploaded file to {target_path}")
        return Success()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to upload file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {str(e)}",
        )


async def _download_file(path: str) -> FileResponse:
    """Internal helper to download a file from the workspace."""
    update_last_execution_time()
    logger.info(f"Downloading file: {path}")
    try:
        target_path = Path(path)
        if not target_path.is_absolute():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path must be absolute",
            )

        if not target_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
            )

        if not target_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Path is not a file"
            )

        return FileResponse(
            path=target_path,
            filename=target_path.name,
            media_type="application/octet-stream",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download file: {str(e)}",
        )


def _create_zip_from_directory(source_dir: Path, output_path: Path) -> None:
    """Create a zip archive for source_dir using only Python stdlib APIs."""
    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(source_dir, source_dir.name)
            for path in sorted(source_dir.rglob("*")):
                archive.write(path, path.relative_to(source_dir.parent))
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


ArchiveFormat = Literal["git-delta", "tar.gz"]

_ARCHIVE_SUFFIX: dict[str, str] = {
    "git-delta": ".patch",
    "tar.gz": ".tar.gz",
}

_ARCHIVE_MEDIA_TYPE: dict[str, str] = {
    "git-delta": "text/x-patch",
    "tar.gz": "application/gzip",
}

ARCHIVE_MANIFEST_NAME = "archive_manifest.json"


# Heavy / generated directories that usually bloat an archive without helping
# eval replay. These are the default excludes, not a minimum exclusion set:
# callers can disable them with ``use_default_excludes=false`` when they need a
# full workspace capture. Both formats apply the same set: git-delta as
# ``:(exclude)`` pathspecs (dropping tracked *and* untracked copies, on top of
# the repo's own .gitignore) and tar.gz by pruning the walk — so the two
# archives capture the same files.
_DEFAULT_ARCHIVE_EXCLUDES = (
    ".git/",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    "dist/",
    "build/",
    ".next/",
    "target/",
    "*.pyc",
)

# Credential-bearing git internals that must NEVER be persisted to a shared
# archive bucket, even for a full capture (``use_default_excludes=false``). A
# tokenized clone (``https://x-access-token:TOKEN@github.com/...``) keeps that
# token in ``.git/config`` and ``.git/FETCH_HEAD``; reflogs and saved
# credentials are equally sensitive. These live under the superproject ``.git``
# AND under ``.git/modules/<name>`` for submodules, so the match is by position
# within any ``.git`` directory rather than a fixed top-level path. (git-delta
# is a working-tree diff and never includes ``.git``, so this only matters for
# tar.gz.)
_SENSITIVE_GIT_INTERNALS = frozenset(
    {"config", "config.worktree", "credentials", ".git-credentials", "FETCH_HEAD"}
)


def _is_sensitive_git_internal(rel: PurePosixPath) -> bool:
    """True if ``rel`` is a credential-bearing file inside any ``.git`` dir.

    Covers the superproject (``.git/...``) and submodules
    (``.git/modules/<name>/...``) at any depth: the sensitive config/credential
    files and every reflog under a ``logs/`` subtree.
    """
    parts = rel.parts
    for i, part in enumerate(parts):
        if part == ".git":
            internal = parts[i + 1 :]
            if not internal:
                return False
            if "logs" in internal:
                return True
            return internal[-1] in _SENSITIVE_GIT_INTERNALS
    return False


# Directory names never worth descending when auto-resolving the repo root under
# a workspace path: they can legitimately contain vendored/nested git repos that
# must not be mistaken for the workspace's own repo.
_ARCHIVE_DESCENT_SKIP = frozenset({"node_modules", "venv", "site-packages", "vendor"})


def _resolve_git_repo_root(target: Path, max_depth: int = 3) -> Path:
    """Resolve the git work-tree to archive under ``target``.

    Callers pass the workspace base (e.g. ``/workspace/project``), but a
    repository-backed conversation clones into a subdirectory
    (``{base}/[{group}/]{repo_name}``), so the base itself is usually *not* a git
    repo. ``git rev-parse`` only searches upward, so archiving the base would
    400 even though the repo sits one or two levels below it — silently
    capturing nothing for exactly the repo-backed conversations the archive
    exists for.

    If ``target`` is already a git work-tree, return it unchanged. Otherwise do a
    bounded depth-first search of its descendants (skipping hidden and
    vendored directories, and not descending into a repo once found) for git
    work-tree roots. Return the unique match if there is exactly one; if zero or
    several are found, return ``target`` unchanged so the existing not-a-git-repo
    handling (HTTP 400) applies rather than guessing.
    """
    if (target / ".git").exists():
        return target
    found: list[Path] = []
    frontier: list[tuple[Path, int]] = [(target, 0)]
    while frontier:
        current, depth = frontier.pop()
        if depth >= max_depth:
            continue
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name.startswith(".") or child.name in _ARCHIVE_DESCENT_SKIP:
                continue
            try:
                if child.is_symlink() or not child.is_dir():
                    continue
            except OSError:
                continue
            if (child / ".git").exists():
                found.append(child)
                if len(found) > 1:
                    # Ambiguous — don't guess which repo to archive.
                    logger.warning(
                        f"Multiple git repos under {target}; cannot pick one to "
                        "archive — using the path as given. Caller should pass "
                        "the repo path explicitly."
                    )
                    return target
            else:
                frontier.append((child, depth + 1))
    return found[0] if len(found) == 1 else target


def _excludes_to_git_pathspecs(excludes: list[str]) -> list[str]:
    """Translate the archive excludes into git ``:(exclude)`` pathspecs.

    Using pathspecs instead of ``core.excludesFile`` drops excluded paths
    whether they are tracked or untracked, so git-delta and tar.gz capture the
    same file set. ``glob`` magic keeps ``*`` from crossing ``/`` while ``**``
    spans directories, mirroring the tar.gz matcher's gitignore semantics: a
    bare name matches at any depth; a multi-segment pattern is anchored at the
    repo root.

    A trailing ``/`` marks a directory-only pattern: we emit only the
    contents form (``.../**``) and NOT the bare entry form, so a regular file
    that happens to share the name (e.g. an authored file literally named
    ``build``) is kept — matching ``_path_is_excluded``'s dir-only carve-out.
    Emitting the bare entry too would silently drop such files from the
    git-delta while the tar.gz keeps them.
    """
    specs: list[str] = []
    for raw in excludes:
        dir_only = raw.endswith("/")
        pat = raw.rstrip("/")
        if not pat:
            continue
        if "/" in pat:
            if not dir_only:
                specs.append(f":(glob,exclude){pat}")
            specs.append(f":(glob,exclude){pat}/**")
        else:
            if not dir_only:
                specs.append(f":(glob,exclude)**/{pat}")
            specs.append(f":(glob,exclude)**/{pat}/**")
    return specs


def _head_is_detached(root: Path) -> bool:
    """True if the repo at ``root`` has a detached HEAD (no current branch)."""
    try:
        branch = run_git_command(
            ["git", "--no-pager", "rev-parse", "--abbrev-ref", "HEAD"], root
        )
    except GitCommandError:
        return False
    return branch.strip() == "HEAD"


def _git_probe(args: list[str], root: Path) -> str:
    """Run a read-only git probe; never logs or raises, returns "" on any failure."""
    try:
        result = run_git_subprocess(["git", "--no-pager", *args], root, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _header_safe(value: str) -> str:
    """Percent-encode a value if needed so it is always a legal ASCII header value."""
    if value.isascii() and value.isprintable():
        return value
    return quote(value, safe="")


def _git_repo_metadata(root: Path) -> dict[str, str]:
    """Best-effort repo identity headers (remote/branch/HEAD) for the archived tree."""
    metadata: dict[str, str] = {}
    remote = _git_probe(["remote", "get-url", "origin"], root)
    if remote:
        metadata["X-Archive-Repo-Remote"] = redact_url_credentials(remote)
    # Combine into one call: --abbrev-ref only applies to args after it, so the
    # first HEAD stays a full sha while the second is abbreviated to the branch.
    head_and_branch = _git_probe(["rev-parse", "HEAD", "--abbrev-ref", "HEAD"], root)
    lines = head_and_branch.splitlines()
    if len(lines) == 2:
        head, branch = lines
        metadata["X-Archive-Head-Commit"] = head
        metadata["X-Archive-Branch"] = "DETACHED" if branch == "HEAD" else branch
    return {k: _header_safe(v) for k, v in metadata.items()}


def _create_git_delta(
    root: Path, base_ref: str | None, output_path: Path, excludes: list[str]
) -> str:
    """Write a git patch capturing the working-tree delta against a base.

    The delta covers tracked modifications, new (untracked) files, and
    deletions relative to ``base_ref`` (defaulting to the auto-detected
    comparison ref — origin branch, merge-base, or the empty tree for a fresh
    repo). A throwaway index (``GIT_INDEX_FILE``) is used so the repository's
    real index is never touched. ``excludes`` are applied as ``:(exclude)``
    pathspecs — on top of the repo's own ``.gitignore`` — so excluded paths are
    dropped whether tracked or untracked, matching the tar.gz format. Callers
    can disable the default excludes when they intentionally want a fuller
    capture.

    Returns the full base commit SHA the patch applies against, or "" when the
    base is the empty tree (fresh repo) or cannot resolve to a commit.
    """
    validate_git_repository(root)
    effective_base = base_ref
    if effective_base is None and _head_is_detached(root):
        # A pinned base-commit checkout (e.g. a SWE-bench-style eval setup)
        # leaves HEAD detached; get_valid_ref then skips the branch strategy and
        # diffs against the remote default-branch TIP, polluting the patch with
        # base..tip. The pinned HEAD commit is the right base, so request it.
        effective_base = "HEAD"
    try:
        ref = get_valid_ref(root, effective_base) or GIT_EMPTY_TREE_HASH
    except GitCommandError as e:
        # An explicit base_ref that does not resolve is client error, not a
        # server fault; surface it so the caller gets a 4xx.
        raise ValueError(f"base_ref {base_ref!r} could not be resolved") from e
    index_path = output_path.with_name(output_path.name + ".index")
    pathspecs = _excludes_to_git_pathspecs(excludes)
    env = {**os.environ, "GIT_INDEX_FILE": str(index_path)}
    try:
        # Seed the scratch index from the base ref, stage the working tree on
        # top of it (skipping the requested excludes), then diff. The ``-- .``
        # pathspec scopes staging and the diff to the requested directory, so a
        # ``path`` that is a subdirectory of a larger repo yields only that
        # subtree's delta rather than the whole repository's.
        subprocess.run(
            ["git", "read-tree", ref],
            cwd=root,
            env=env,
            capture_output=True,
            check=True,
            timeout=60,
        )
        subprocess.run(
            ["git", "add", "-A", "--", ".", *pathspecs],
            cwd=root,
            env=env,
            capture_output=True,
            check=True,
            timeout=300,
        )
        # Stream the diff straight to disk so a large binary patch is never
        # buffered in memory (OOM risk at pause/stop on a fat workspace).
        with open(output_path, "wb") as out:
            subprocess.run(
                ["git", "diff", "--binary", "--cached", ref, "--", ".", *pathspecs],
                cwd=root,
                env=env,
                stdout=out,
                stderr=subprocess.PIPE,
                check=True,
                timeout=300,
            )
    except subprocess.CalledProcessError as e:
        output_path.unlink(missing_ok=True)
        stderr = e.stderr.decode("utf-8", "replace") if e.stderr else ""
        raise GitCommandError(
            message="Failed to generate git delta",
            command=e.cmd if isinstance(e.cmd, list) else [str(e.cmd)],
            exit_code=e.returncode,
            stderr=stderr.strip(),
        ) from e
    except subprocess.TimeoutExpired as e:
        output_path.unlink(missing_ok=True)
        raise GitCommandError(
            message="Timed out generating git delta",
            command=e.cmd if isinstance(e.cmd, list) else [str(e.cmd)],
            exit_code=-1,
            stderr=f"timed out after {e.timeout}s",
        ) from e
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        index_path.unlink(missing_ok=True)

    if ref == GIT_EMPTY_TREE_HASH:
        return ""
    # Resolve the base to a full commit SHA so the artifact is self-describing.
    try:
        return run_git_command(
            ["git", "--no-pager", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            root,
        )
    except GitCommandError:
        return ""


def _path_is_excluded(rel: PurePosixPath, patterns: list[str], is_dir: bool) -> bool:
    """True if ``rel`` is excluded by any glob in ``patterns``.

    Mirrors the git-delta path's gitignore semantics so the two formats agree:

    - A trailing ``/`` marks a directory-only pattern: it matches a directory but
      never a file's own name, so a ``build/`` pattern prunes a ``build``
      directory while keeping a file literally named ``build``.
    - A multi-segment pattern (one containing ``/``, e.g. ``secrets/prod``)
      matches the full relative path or any prefix of it, so it drops both
      ``secrets/prod`` and everything beneath it.
    - A bare-name pattern (no ``/``, e.g. ``node_modules`` or ``*.pyc``) matches
      any single path component, at any depth.

    Dependency-free so no new package is pulled in.
    """
    parts = rel.parts
    for raw in patterns:
        pattern = raw.rstrip("/")
        dir_only = raw.endswith("/")
        if "/" in pattern:
            # Multi-segment: anchor against the relative path root and match
            # per-segment so a ``*`` does NOT cross ``/`` (gitignore semantics,
            # matching the git-delta path). The pattern matches the path itself
            # or anything nested under it: ``secrets/prod`` drops ``secrets/prod``
            # and ``secrets/prod/key``; ``*/test`` drops ``a/test`` but not
            # ``a/b/test``; ``a/*/c`` drops ``a/x/c``.
            pattern_parts = pattern.split("/")
            if len(parts) >= len(pattern_parts) and all(
                fnmatch.fnmatch(part, pat) for part, pat in zip(parts, pattern_parts)
            ):
                # A dir-only pattern matches the directory and anything nested
                # under it, but not a file whose own path equals the pattern.
                if dir_only and not is_dir and len(parts) == len(pattern_parts):
                    continue
                return True
        else:
            # Bare name: match any single component. For a dir-only pattern on a
            # file, skip the basename so a file sharing a directory exclude's
            # name is not dropped.
            candidates = parts if (is_dir or not dir_only) else parts[:-1]
            for part in candidates:
                if fnmatch.fnmatch(part, pattern):
                    return True
    return False


def _build_archive_manifest(
    source: str, file_count: int, total_bytes: int, excludes: list[str]
) -> bytes:
    """Deterministic (timestamp-free) JSON manifest embedded in a tar.gz."""
    manifest = {
        "format": "tar.gz",
        "source": source,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "excludes": excludes,
    }
    return json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")


class _ExactSizeReader:
    """File wrapper that always yields exactly ``size`` bytes.

    ``tar.addfile`` writes the member header (declaring ``size``) and then copies
    bytes from the file; if a concurrent writer truncates the file in between, a
    short read would raise *mid-member*, after the header and a partial,
    unpadded body are already on the gzip stream — silently misaligning every
    member that follows (the trailing manifest included). By padding a short read
    with NUL (file shrank) and stopping at ``size`` (file grew), the bytes handed
    to ``addfile`` always match the declared size, so the stream can never
    desynchronize no matter how the workspace mutates during capture.
    """

    def __init__(self, fileobj: IO[bytes], size: int) -> None:
        self._fileobj = fileobj
        self._remaining = size

    def read(self, n: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        if n is None or n < 0 or n > self._remaining:
            n = self._remaining
        data = self._fileobj.read(n)
        if len(data) < n:
            data = data + b"\x00" * (n - len(data))
        self._remaining -= len(data)
        return data


def _add_file_member(tar: tarfile.TarFile, file_path: Path, arcname: str) -> int | None:
    """Add a regular file to ``tar`` without risking tar-stream corruption.

    ``tar.add`` stats the file, writes a header for that size, then copies the
    body — a concurrent truncate between the two desynchronizes the stream (see
    ``_ExactSizeReader``). Instead, size the header from the OPEN fd and copy
    exactly that many bytes via ``_ExactSizeReader``. Returns the byte count, or
    ``None`` if the file could not be read / was not a regular file (skipped,
    best-effort — the workspace may still be mutating).
    """
    try:
        f = open(file_path, "rb")
    except OSError as e:
        logger.warning(f"Skipping unreadable file {file_path}: {e}")
        return None
    try:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            # Raced into a non-regular path (dir/fifo/socket); skip it.
            return None
        info = tar.gettarinfo(arcname=arcname, fileobj=f)
        tar.addfile(info, _ExactSizeReader(f, info.size))
        return info.size
    except OSError as e:
        logger.warning(f"Skipping unreadable file {file_path}: {e}")
        return None
    finally:
        f.close()


def _create_tar_gz_archive(root: Path, output_path: Path, excludes: list[str]) -> None:
    """Stream a gzip tarball of ``root`` to ``output_path``.

    Walks ``root`` without following symlinks, pruning excluded directories so
    they are never descended into, and adds regular files only (symlinks are
    skipped — same safety posture as the rest of the endpoint). Files are added
    one at a time so the archive is never held in memory. A deterministic
    manifest member (``ARCHIVE_MANIFEST_NAME``) records what was captured.
    """
    file_count = 0
    total_bytes = 0
    manifest_arcname = f"{root.name}/{ARCHIVE_MANIFEST_NAME}"
    manifest_collision = False
    try:
        with tarfile.open(output_path, "w:gz") as tar:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                base = PurePosixPath(Path(dirpath).relative_to(root).as_posix())
                # Prune excluded directories in place so we never descend them.
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not _path_is_excluded(base / d, excludes, is_dir=True)
                    and not _is_sensitive_git_internal(base / d)
                ]
                # Emit a directory member for every surviving directory so empty
                # authored directories round-trip (the byte-for-byte full-capture
                # contract). tar would otherwise only recreate parents implied by
                # file members, dropping childless directories entirely.
                dir_arcname = (
                    root.name if base == PurePosixPath(".") else f"{root.name}/{base}"
                )
                if not Path(dirpath).is_symlink():
                    # Guard like the per-file add below: a directory that vanishes
                    # mid-capture (concurrent mutation) must skip its own member,
                    # not abort the whole archive.
                    try:
                        tar.add(dirpath, arcname=dir_arcname, recursive=False)
                    except OSError as e:
                        logger.warning(f"Skipping unreadable directory {dirpath}: {e}")
                for name in filenames:
                    rel = base / name
                    if _path_is_excluded(
                        rel, excludes, is_dir=False
                    ) or _is_sensitive_git_internal(rel):
                        continue
                    file_path = Path(dirpath) / name
                    if file_path.is_symlink() or not file_path.is_file():
                        continue
                    arcname = f"{root.name}/{rel}"
                    # Best-effort and corruption-proof: the workspace may still be
                    # mutating, so a file that vanishes or is truncated mid-add is
                    # skipped without desynchronizing the tar stream.
                    size = _add_file_member(tar, file_path, arcname)
                    if size is None:
                        continue
                    if arcname == manifest_arcname:
                        manifest_collision = True
                    file_count += 1
                    total_bytes += size

            # Skip our synthetic capture manifest when the workspace already has a
            # real file at that path: the user's data must win over our metadata.
            if manifest_collision:
                logger.warning(
                    f"Workspace already contains {ARCHIVE_MANIFEST_NAME!r}; "
                    "preserving the user's file and skipping the synthetic "
                    "archive manifest."
                )
            else:
                manifest = _build_archive_manifest(
                    root.name, file_count, total_bytes, excludes
                )
                info = tarfile.TarInfo(manifest_arcname)
                info.size = len(manifest)
                tar.addfile(info, io.BytesIO(manifest))
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


@file_router.post("/upload")
async def upload_file_query(
    path: Annotated[str, Query(description="Absolute file path")],
    file: Annotated[UploadFile, File()],
) -> Success:
    """Upload a file to the workspace using query parameter (preferred method)."""
    return await _upload_file(path, file)


@file_router.get("/download")
async def download_file_query(
    path: Annotated[str, Query(description="Absolute file path")],
) -> FileResponse:
    """Download a file from the workspace using query parameter (preferred method)."""
    return await _download_file(path)


def _list_home_favorites(
    home: Path, limit: int = 50, include_hidden: bool = False
) -> list[FileBrowserEntry]:
    """Top-level directories inside the user's home, alphabetised.

    Symlinks are skipped. Hidden entries (names starting with '.') are skipped
    unless ``include_hidden`` is True, so the list matches what
    ``search_subdirs`` returns for the same path and the same flag.
    """
    entries: list[FileBrowserEntry] = []
    try:
        with os.scandir(home) as scanner:
            for entry in scanner:
                if not include_hidden and entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                entries.append(
                    FileBrowserEntry(label=entry.name, path=str(home / entry.name))
                )
    except (PermissionError, FileNotFoundError):
        return []
    entries.sort(key=lambda e: e.label.lower())
    return entries[:limit]


def _list_root_locations() -> list[FileBrowserEntry]:
    """Filesystem roots: present drives on Windows, '/' on POSIX."""
    if os.name == "nt":
        from string import ascii_uppercase

        roots: list[FileBrowserEntry] = []
        for letter in ascii_uppercase:
            candidate = Path(f"{letter}:\\")
            try:
                if candidate.exists():
                    roots.append(
                        FileBrowserEntry(label=f"{letter}:", path=str(candidate))
                    )
            except OSError:
                continue
        return roots
    return [FileBrowserEntry(label="/", path="/")]


@file_router.get("/home")
async def get_home_directory(
    include_hidden: Annotated[
        bool,
        Query(description="Include hidden top-level directories in `favorites`"),
    ] = False,
) -> HomeResponse:
    """Return the agent-server user's home directory and dynamic sidebar lists.

    ``favorites`` is the set of top-level directories actually present in the
    user's home (so it reflects the real environment instead of a hardcoded
    list of names that may not exist). Hidden directories are included only
    when ``include_hidden`` is True. ``locations`` is the set of filesystem
    roots — '/' on POSIX or available drive letters on Windows.
    """
    home = Path.home()
    return HomeResponse(
        home=str(home),
        favorites=_list_home_favorites(home, include_hidden=include_hidden),
        locations=_list_root_locations(),
    )


@file_router.get("/search_subdirs")
async def search_subdirs(
    path: Annotated[
        str,
        Query(description="Absolute directory path to list subdirectories of"),
    ],
    page_id: Annotated[
        str | None,
        Query(title="Optional next_page_id from the previously returned page"),
    ] = None,
    limit: Annotated[
        int,
        Query(title="The max number of results in the page", gt=0, lte=100),
    ] = 100,
    include_hidden: Annotated[
        bool,
        Query(title="Include hidden subdirectories (names starting with '.')"),
    ] = False,
) -> SubdirectoryPage:
    """Search / List immediate subdirectories of `path`.

    Used by the GUI's workspace picker. Symlinks and files are skipped. Hidden
    entries (names starting with '.') are skipped unless ``include_hidden`` is
    True. Returns absolute paths so the GUI can use a result directly as
    ``workspace.working_dir``.

    Results are sorted case-insensitively by name and paginated. ``page_id`` is
    the ``next_page_id`` returned by the previous page (the lowercase name of
    the first item to include on the next page).
    """
    assert limit > 0
    assert limit <= 100

    target = Path(path)
    if not target.is_absolute():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must be absolute",
        )
    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Directory not found",
        )
    if not target.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a directory",
        )

    entries: list[SubdirectoryEntry] = []
    try:
        with os.scandir(target) as scanner:
            for entry in scanner:
                if not include_hidden and entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                entries.append(
                    SubdirectoryEntry(name=entry.name, path=str(target / entry.name))
                )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {e}",
        )

    entries.sort(key=lambda e: e.name.lower())

    start_index = 0
    if page_id:
        for i, entry in enumerate(entries):
            if entry.name.lower() == page_id:
                start_index = i
                break

    page_items = entries[start_index : start_index + limit]
    next_page_id: str | None = None
    if start_index + limit < len(entries):
        next_page_id = entries[start_index + limit].name.lower()

    return SubdirectoryPage(items=page_items, next_page_id=next_page_id)


@file_router.get("/download-trajectory/{conversation_id}")
async def download_trajectory(
    conversation_id: UUID,
) -> FileResponse:
    """Download a zip archive of a conversation trajectory."""
    config = get_default_config()
    temp_file = config.conversations_path / f"{conversation_id.hex}.zip"
    conversation_dir = config.conversations_path / conversation_id.hex

    if not conversation_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    await asyncio.to_thread(_create_zip_from_directory, conversation_dir, temp_file)
    return FileResponse(
        path=temp_file,
        filename=temp_file.name,
        media_type="application/octet-stream",
        background=BackgroundTask(temp_file.unlink),
    )


@file_router.get("/archive")
async def archive_directory(
    path: Annotated[
        str, Query(description="Absolute path of the directory to archive")
    ],
    archive_format: Annotated[
        ArchiveFormat,
        Query(
            alias="format",
            description=(
                "Archive format: 'git-delta' for a git patch of the working-tree "
                "changes against a base ref (requires a git repository); 'tar.gz' "
                "for a full gzip tarball of the entire directory (works on "
                "non-git directories too)."
            ),
        ),
    ] = "git-delta",
    base_ref: Annotated[
        str | None,
        Query(
            description=(
                "Only for format='git-delta': base ref to diff against. "
                "Defaults to the auto-detected comparison ref (origin branch, "
                "merge-base, or the empty tree for a fresh repo). Ignored for "
                "format='tar.gz'."
            )
        ),
    ] = None,
    use_default_excludes: Annotated[
        bool,
        Query(
            description=(
                "Whether to apply the built-in default exclude patterns "
                "(node_modules/, .venv/, caches, build outputs, etc.). Defaults "
                "to true for compact archives; set false to allow a full "
                "workspace capture, including fat directories."
            )
        ),
    ] = True,
    exclude: Annotated[
        list[str] | None,
        Query(
            description=(
                "Additional glob patterns to exclude (repeatable, e.g. "
                "?exclude=foo&exclude=*.bin). Combined with the built-in "
                "default excludes when use_default_excludes=true; used by "
                "itself when use_default_excludes=false."
            )
        ),
    ] = None,
) -> FileResponse:
    """Archive a workspace directory for persistence before runtime deletion.

    Produces a downloadable archive of ``path``. Symlinks are never followed
    out of the requested directory, so the archive cannot include files from
    outside it. The temporary archive is created outside ``path`` and removed
    after the response is sent.
    """
    update_last_execution_time()

    target = Path(path)
    if not target.is_absolute():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must be absolute",
        )
    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Directory not found",
        )
    if not target.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a directory",
        )
    if base_ref is not None and base_ref.startswith("-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="base_ref must not start with '-'",
        )

    target = target.resolve()
    effective_excludes = (
        list(_DEFAULT_ARCHIVE_EXCLUDES) if use_default_excludes else []
    ) + (exclude or [])
    # Build the archive on the same volume as the workspace (its parent dir, so
    # it is never included in itself) rather than the system temp dir: a large
    # tar.gz on a tmpfs /tmp would defeat the stream-to-disk OOM fix, and on a
    # small container root it could trip the pod's ephemeral-storage limit. Fall
    # back to the default temp location if the parent is not usable.
    scratch_dir: str | None = None
    parent = target.parent
    if parent != target and os.access(parent, os.W_OK):
        scratch_dir = str(parent)
    fd, tmp_name = tempfile.mkstemp(
        suffix=_ARCHIVE_SUFFIX[archive_format], dir=scratch_dir
    )
    os.close(fd)
    output_path = Path(tmp_name)

    # The caller passes the workspace base, but a repo-backed conversation clones
    # into a subdirectory; resolve to the actual repo so both formats root the
    # archive at the repo (consistent paths across git-delta, tar.gz, and the
    # initial snapshot) and git-delta does not 400 on the non-repo parent.
    repo_root = await asyncio.to_thread(_resolve_git_repo_root, target)
    base_commit = ""
    try:
        if archive_format == "git-delta":
            base_commit = await asyncio.to_thread(
                _create_git_delta,
                repo_root,
                base_ref,
                output_path,
                effective_excludes,
            )
        else:
            await asyncio.to_thread(
                _create_tar_gz_archive, repo_root, output_path, effective_excludes
            )
    except GitRepositoryError as e:
        output_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not a git repository: {e}",
        )
    except ValueError as e:
        output_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except GitCommandError as e:
        output_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate git delta: {e}",
        )
    except Exception as e:
        output_path.unlink(missing_ok=True)
        logger.error(f"Failed to archive {target}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to archive directory: {str(e)}",
        )

    # Repo identity (remote / branch / HEAD) applies to both formats so a
    # tar.gz snapshot is self-describing too; base_commit/base_ref are
    # git-delta-only (they describe the patch's replay base).
    headers: dict[str, str] = {}
    if (repo_root / ".git").exists():
        headers.update(await asyncio.to_thread(_git_repo_metadata, repo_root))
    if base_commit:
        # Make a git-delta self-describing so consumers can replay the patch.
        headers["X-Archive-Base-Commit"] = base_commit
        headers["X-Archive-Base-Ref"] = base_ref or "auto"

    return FileResponse(
        path=output_path,
        filename=f"{repo_root.name}{_ARCHIVE_SUFFIX[archive_format]}",
        media_type=_ARCHIVE_MEDIA_TYPE[archive_format],
        headers=headers,
        background=BackgroundTask(output_path.unlink, missing_ok=True),
    )
