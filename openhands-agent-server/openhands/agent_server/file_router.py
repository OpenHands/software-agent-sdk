import asyncio
import io
import json
import os
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated, Literal
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
    validate_git_repository,
)
from openhands.sdk.logger import get_logger


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


ArchiveFormat = Literal["tar.gz", "zip", "git-delta"]

ARCHIVE_MANIFEST_NAME = "archive_manifest.json"

_ARCHIVE_SUFFIX: dict[str, str] = {
    "tar.gz": ".tar.gz",
    "zip": ".zip",
    "git-delta": ".patch",
}

_ARCHIVE_MEDIA_TYPE: dict[str, str] = {
    "tar.gz": "application/gzip",
    "zip": "application/zip",
    "git-delta": "text/x-patch",
}


def _collect_workspace_files(root: Path) -> list[tuple[Path, Path]]:
    """Regular files under ``root`` as ``(absolute_path, arcname)``, sorted.

    ``os.walk(followlinks=False)`` never descends into symlinked directories,
    and symlinked files are skipped, so a symlink cannot pull a file from
    outside ``root`` into the archive and there is no risk of a symlink cycle.
    """
    files: list[tuple[Path, Path]] = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            abs_path = Path(dirpath) / name
            if abs_path.is_symlink():
                continue
            files.append((abs_path, abs_path.relative_to(root)))
    files.sort(key=lambda pair: str(pair[1]))
    return files


def _build_archive_manifest(
    root: Path, fmt: ArchiveFormat, files: list[tuple[Path, Path]]
) -> bytes:
    """Deterministic JSON manifest embedded at the archive root.

    No timestamp is included so the manifest is reproducible for an identical
    tree; callers that persist the archive record the capture time alongside
    the stored object.
    """
    total_bytes = 0
    for abs_path, _arcname in files:
        try:
            total_bytes += abs_path.stat().st_size
        except OSError:
            continue
    manifest = {
        "format": fmt,
        "source": str(root),
        "file_count": len(files),
        "total_bytes": total_bytes,
    }
    return json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")


def _create_tar_gz_archive(
    files: list[tuple[Path, Path]], manifest: bytes, output_path: Path
) -> None:
    try:
        with tarfile.open(output_path, "w:gz") as tar:
            for abs_path, arcname in files:
                tar.add(abs_path, arcname=str(arcname), recursive=False)
            info = tarfile.TarInfo(name=ARCHIVE_MANIFEST_NAME)
            info.size = len(manifest)
            tar.addfile(info, io.BytesIO(manifest))
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def _create_zip_archive(
    files: list[tuple[Path, Path]], manifest: bytes, output_path: Path
) -> None:
    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for abs_path, arcname in files:
                archive.write(abs_path, str(arcname))
            archive.writestr(ARCHIVE_MANIFEST_NAME, manifest)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


# Heavy / generated directories that bloat a delta without helping eval replay.
# Applied via a scratch ``core.excludesFile`` so they are skipped even when the
# repo itself does not ``.gitignore`` them (the repo's own .gitignore still
# applies on top of this).
_GIT_DELTA_DEFAULT_EXCLUDES = (
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


def _create_git_delta(root: Path, base_ref: str | None, output_path: Path) -> str:
    """Write a git patch capturing the working-tree delta against a base.

    The delta covers tracked modifications, new (untracked) files, and
    deletions relative to ``base_ref`` (defaulting to the auto-detected
    comparison ref — origin branch, merge-base, or the empty tree for a fresh
    repo). A throwaway index (``GIT_INDEX_FILE``) is used so the repository's
    real index is never touched. Heavy generated/dependency directories
    (``_GIT_DELTA_DEFAULT_EXCLUDES``, e.g. ``node_modules/``) are excluded —
    on top of the repo's own ``.gitignore`` — so the delta stays compact for
    eval replay even if such a directory is present but not git-ignored.

    Returns the full base commit SHA the patch applies against, or "" when the
    base is the empty tree (fresh repo) or cannot resolve to a commit.
    """
    validate_git_repository(root)
    try:
        ref = get_valid_ref(root, base_ref) or GIT_EMPTY_TREE_HASH
    except GitCommandError as e:
        # An explicit base_ref that does not resolve is client error, not a
        # server fault; surface it so the caller gets a 4xx.
        raise ValueError(f"base_ref {base_ref!r} could not be resolved") from e
    index_path = output_path.with_name(output_path.name + ".index")
    excludes_path = output_path.with_name(output_path.name + ".excludes")
    excludes_path.write_text("\n".join(_GIT_DELTA_DEFAULT_EXCLUDES) + "\n")
    env = {**os.environ, "GIT_INDEX_FILE": str(index_path)}
    try:
        # Seed the scratch index from the base ref, stage the working tree on
        # top of it (skipping the default excludes), then diff. The ``-- .``
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
            ["git", "-c", f"core.excludesFile={excludes_path}", "add", "-A", "--", "."],
            cwd=root,
            env=env,
            capture_output=True,
            check=True,
            timeout=300,
        )
        result = subprocess.run(
            ["git", "diff", "--binary", "--cached", ref, "--", "."],
            cwd=root,
            env=env,
            capture_output=True,
            check=True,
            timeout=300,
        )
        output_path.write_bytes(result.stdout)
    except subprocess.CalledProcessError as e:
        output_path.unlink(missing_ok=True)
        stderr = e.stderr.decode("utf-8", "replace") if e.stderr else ""
        raise GitCommandError(
            message="Failed to generate git delta",
            command=e.cmd if isinstance(e.cmd, list) else [str(e.cmd)],
            exit_code=e.returncode,
            stderr=stderr.strip(),
        ) from e
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        index_path.unlink(missing_ok=True)
        excludes_path.unlink(missing_ok=True)

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


def _build_workspace_archive(
    root: Path, fmt: ArchiveFormat, base_ref: str | None, output_path: Path
) -> str | None:
    """Build the requested archive of ``root`` at ``output_path`` (blocking).

    Returns the git-delta base commit SHA (or "" if none), and None for the
    tar.gz / zip formats.
    """
    if fmt == "git-delta":
        return _create_git_delta(root, base_ref, output_path)
    files = _collect_workspace_files(root)
    manifest = _build_archive_manifest(root, fmt, files)
    if fmt == "tar.gz":
        _create_tar_gz_archive(files, manifest, output_path)
    else:
        _create_zip_archive(files, manifest, output_path)
    return None


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
    format: Annotated[
        ArchiveFormat,
        Query(
            description=(
                "Archive format: 'tar.gz' (default) or 'zip' for a full file "
                "archive, or 'git-delta' for a git patch of the working-tree "
                "changes against a base ref (requires a git repository)."
            )
        ),
    ] = "tar.gz",
    base_ref: Annotated[
        str | None,
        Query(
            description=(
                "Only for format='git-delta': base ref to diff against. "
                "Defaults to the auto-detected comparison ref (origin branch, "
                "merge-base, or the empty tree for a fresh repo)."
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
    # Build the archive outside the workspace so it is never included in itself
    # and leaves nothing behind in the archived tree.
    fd, tmp_name = tempfile.mkstemp(suffix=_ARCHIVE_SUFFIX[format])
    os.close(fd)
    output_path = Path(tmp_name)

    try:
        base_commit = await asyncio.to_thread(
            _build_workspace_archive, target, format, base_ref, output_path
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

    headers: dict[str, str] | None = None
    if base_commit:
        # Make a git-delta self-describing so consumers can replay the patch.
        headers = {
            "X-Archive-Base-Commit": base_commit,
            "X-Archive-Base-Ref": base_ref or "auto",
        }

    return FileResponse(
        path=output_path,
        filename=f"{target.name}{_ARCHIVE_SUFFIX[format]}",
        media_type=_ARCHIVE_MEDIA_TYPE[format],
        headers=headers,
        background=BackgroundTask(output_path.unlink, missing_ok=True),
    )
