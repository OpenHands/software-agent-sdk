"""Allowlisted export/import of an ACP CLI's session-transcript subtree.

The opaque files an ACP CLI needs for native ``session/load`` resume (Codex
``sessions/**``, Claude Code ``projects/**``) live under the per-conversation
data root ``<persistence_dir>/acp/<provider.key>`` (see
``ACPAgent.acp_isolate_data_dir`` / ``_materialise_file_secrets``). That root
also holds live credentials (``auth.json``, ``.credentials.json``) and global
state (``history.jsonl``, config, caches), so a snapshot must never tar the
whole root: both directions are restricted to the provider's
:attr:`~openhands.sdk.settings.acp_providers.ACPProviderInfo.session_subtrees`
allowlist — fail-closed, on export *and* import. Auth is re-established on
every launch by materialisation / env secrets, so excluding it loses nothing.

Import is seed-if-absent per file (a live copy on the conversation volume is
never clobbered — the blob is a strict fallback for a recycled sandbox) and
path-safe (only regular files and directories, no absolute paths, no ``..``,
no links or devices).
"""

import io
import os
import tarfile
from pathlib import Path, PurePosixPath

from openhands.sdk.logger import get_logger
from openhands.sdk.settings.acp_providers import ACPProviderInfo


logger = get_logger(__name__)

__all__ = ["export_acp_session_blob", "import_acp_session_blob"]


def _allowlisted_relpath(
    name: str, session_subtrees: tuple[str, ...]
) -> PurePosixPath | None:
    """Validate one archive member name against the allowlist.

    Returns the normalized relative path when *name* is a safe path under one
    of *session_subtrees*; ``None`` when it is outside the allowlist.
    Raises :class:`ValueError` for path-traversal shapes (absolute, ``..``,
    empty) — those indicate a corrupt or malicious archive, not a benign skip.
    """
    pure = PurePosixPath(name)
    if pure.is_absolute() or not pure.parts:
        raise ValueError(f"unsafe path in session blob: {name!r}")
    if any(part in ("..", "") for part in pure.parts):
        raise ValueError(f"unsafe path in session blob: {name!r}")
    if pure.parts[0] not in session_subtrees:
        return None
    return pure


def export_acp_session_blob(data_root: Path, provider: ACPProviderInfo) -> bytes | None:
    """Tar-gzip the provider's session subtrees under *data_root*.

    Packs only regular files under ``data_root/<subtree>`` for each subtree in
    :attr:`~ACPProviderInfo.session_subtrees` (archive names relative to
    *data_root*, e.g. ``sessions/2026/.../rollout-….jsonl``). Symlinks and
    anything outside the allowlist are never included, so credentials at the
    data root cannot leak into the blob by construction.

    Returns ``None`` when the provider has no session subtrees (e.g.
    gemini-cli) or no session files exist yet.
    """
    if not provider.session_subtrees:
        return None
    data_root_real = data_root.resolve()
    files: list[tuple[Path, str]] = []
    for subtree in provider.session_subtrees:
        base = data_root / subtree
        # The data root is the CLI's own writable HOME (it also holds auth.json
        # / .credentials.json / history.jsonl), so a malicious/prompt-injected
        # agent could `ln -sf $CODEX_HOME sessions` to smuggle credentials under
        # the allowlisted prefix. ``os.walk(followlinks=False)`` only refuses to
        # *descend* into symlinked sub-dirs — it still lists the contents of a
        # symlinked BASE — and the per-file ``is_symlink`` check misses real
        # files reached *through* a symlinked dir. So reject a symlinked base
        # outright and realpath-verify every emitted file stays under it.
        if base.is_symlink() or not base.is_dir():
            continue
        base_real = base.resolve()
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            # Don't descend into symlinked directories either.
            dirnames[:] = [
                d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
            ]
            for filename in filenames:
                path = Path(dirpath) / filename
                if path.is_symlink() or not path.is_file():
                    continue
                # Defense in depth: the file's real location must be inside the
                # allowlisted subtree (and thus the data root) — never a path a
                # symlink redirected out of it.
                file_real = path.resolve()
                if not file_real.is_relative_to(base_real):
                    continue
                if not file_real.is_relative_to(data_root_real):
                    continue
                arcname = path.relative_to(data_root).as_posix()
                files.append((path, arcname))
    if not files:
        return None
    files.sort(key=lambda item: item[1])
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, arcname in files:
            info = tar.gettarinfo(path, arcname=arcname)
            if not info.isreg():
                continue
            # Strip host-specific ownership; permissions are reapplied on import.
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with path.open("rb") as fileobj:
                tar.addfile(info, fileobj)
    logger.info(
        "Exported ACP session blob for %s: %d file(s) from %s",
        provider.key,
        len(files),
        data_root,
    )
    return buffer.getvalue()


def import_acp_session_blob(
    data_root: Path, provider: ACPProviderInfo, blob: bytes
) -> int:
    """Seed-if-absent extraction of a session blob into *data_root*.

    Re-applies the :attr:`~ACPProviderInfo.session_subtrees` allowlist on the
    way in (a blob is external input — never trust its manifest), accepts only
    regular files, and skips any file that already exists non-empty so a live
    copy on the conversation volume always wins over the snapshot.

    Returns the number of files written. Raises :class:`ValueError` for an
    archive containing unsafe paths or non-file members under the allowlist.
    """
    if not provider.session_subtrees:
        return 0
    data_root_real = data_root.resolve()
    written = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar:
            relpath = _allowlisted_relpath(member.name, provider.session_subtrees)
            if relpath is None:
                logger.warning(
                    "Skipping session-blob member outside allowlist for %s: %s",
                    provider.key,
                    member.name,
                )
                continue
            if member.isdir():
                continue
            if not member.isreg():
                raise ValueError(
                    f"unsupported member type in session blob: {member.name!r}"
                )
            target = data_root.joinpath(*relpath.parts)
            # The member name is allowlist-safe, but the on-disk write still
            # follows pre-existing symlinks. A symlinked subtree dir planted in
            # the sandbox before restore (e.g. ``sessions`` -> ``/workspace``)
            # would redirect the write outside the data root. Resolve the parent
            # (resolve() walks existing symlinks; the not-yet-created tail stays
            # literal) and refuse anything that escapes, and never write through
            # a target that is itself an existing symlink.
            if not target.parent.resolve().is_relative_to(data_root_real):
                raise ValueError(
                    f"session blob target escapes data root: {member.name!r}"
                )
            if target.is_symlink():
                raise ValueError(f"refusing to write through symlink: {member.name!r}")
            if target.is_file() and target.stat().st_size > 0:
                continue  # seed-if-absent: never clobber live state
            extracted = tar.extractfile(member)
            if extracted is None:  # pragma: no cover - isreg() guarantees a stream
                continue
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with extracted:
                target.write_bytes(extracted.read())
            os.chmod(target, 0o600)
            written += 1
    logger.info(
        "Imported ACP session blob for %s: %d file(s) into %s",
        provider.key,
        written,
        data_root,
    )
    return written
