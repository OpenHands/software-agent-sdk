"""Clear the PF_X bit from PT_GNU_STACK on shared libraries.

Some prebuilt ELF shared libraries ship with ``PT_GNU_STACK`` flagged as
executable (``PF_X``) even though they do not actually need an executable
stack. Notably, ``libpython3.13.so.1.0`` as distributed by
``python-build-standalone`` (used by ``uv python install``) has this flag set.

Under Debian's glibc ``2.41-12+deb13u2`` (Trixie) NX enforcement and under
Docker-in-Docker with seccomp restrictions (GitHub Actions, sysbox-runc), the
dynamic linker refuses to load such libraries with::

    cannot enable executable stack as shared object requires: Invalid argument

This module addresses the problem at its actual layer (ELF program headers)
rather than dodging ``python-build-standalone``. It walks a directory tree,
finds every ``.so*`` file, parses each ELF's program header table, and clears
the ``PF_X`` bit on any ``PT_GNU_STACK`` entry that has it set.

The helper is:

* **Idempotent.** Re-running it on a directory that has already been sanitized
  is a no-op.
* **A no-op** on ELFs that do not have a ``PT_GNU_STACK`` program header, or on
  ones where ``PT_GNU_STACK`` already has ``PF_X`` cleared.
* **Architecture-agnostic.** Supports 32-bit and 64-bit ELF, little- and
  big-endian, and respects the program-header layout differences between
  ``Elf32_Phdr`` and ``Elf64_Phdr``.
* **Strip-safe.** It only rewrites a single ``uint32`` inside an existing
  program header; the segment table and section headers are untouched, so
  running ``strip`` afterwards is fine.
* **Dual-use.** Importable as ``from clear_execstack import clear_execstack``
  or runnable as ``python clear_execstack.py <path>``.

Usage::

    # CLI — walk a directory tree
    python clear_execstack.py /agent-server/uv-managed-python

    # Library — single file or directory
    from clear_execstack import clear_execstack, clear_execstack_in_tree
    clear_execstack("/path/to/libpython3.13.so.1.0")
    clear_execstack_in_tree("/agent-server/uv-managed-python")
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path


# ELF constants — see <elf.h>.
_ELF_MAGIC = b"\x7fELF"
_ELFCLASS32 = 1
_ELFCLASS64 = 2
_ELFDATA2LSB = 1
_ELFDATA2MSB = 2
_PT_GNU_STACK = 0x6474E551
_PF_X = 0x1


def _is_shared_object(name: str) -> bool:
    """Return True if ``name`` looks like a shared-object filename.

    Matches ``foo.so``, ``foo.so.1``, ``foo.so.1.0``, etc. Deliberately ignores
    ``.py``, ``.pyc``, and other non-ELF files so the caller can pass any
    directory and have the helper pick only relevant payloads.
    """
    if ".so" not in name:
        return False
    # Must contain a ".so" path component followed by either end-of-string or
    # a version suffix ("libfoo.so", "libfoo.so.1.2.3"). Reject things like
    # "foo.sources" that happen to contain ".so".
    parts = name.split(".")
    for i, part in enumerate(parts):
        if part == "so":
            remainder = parts[i + 1 :]
            if not remainder or all(p.isdigit() for p in remainder):
                return True
    return False


def clear_execstack(path: str | os.PathLike[str]) -> bool:
    """Clear ``PF_X`` from ``PT_GNU_STACK`` in the ELF at ``path``.

    Returns ``True`` if a byte was modified, ``False`` otherwise (not an ELF,
    no ``PT_GNU_STACK`` entry, or ``PF_X`` already cleared).

    Raises nothing for non-ELF files — they are silently skipped, so the
    caller can walk a tree indiscriminately.
    """
    path = os.fspath(path)
    # Skip symlinks — we sanitize the real target once rather than chasing
    # every alias and risking double-open issues.
    if os.path.islink(path):
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        with os.fdopen(fd, "r+b") as f:
            ident = f.read(16)
            if len(ident) < 16 or ident[:4] != _ELF_MAGIC:
                return False
            ei_class = ident[4]
            ei_data = ident[5]
            if ei_class not in (_ELFCLASS32, _ELFCLASS64):
                return False
            if ei_data == _ELFDATA2LSB:
                endian = "<"
            elif ei_data == _ELFDATA2MSB:
                endian = ">"
            else:
                return False

            if ei_class == _ELFCLASS64:
                # Elf64_Ehdr: e_phoff @ 32 (u64), e_phentsize @ 54 (u16),
                # e_phnum @ 56 (u16). Program header: p_type @ 0 (u32),
                # p_flags @ 4 (u32).
                f.seek(32)
                (e_phoff,) = struct.unpack(endian + "Q", f.read(8))
                f.seek(54)
                e_phentsize, e_phnum = struct.unpack(endian + "HH", f.read(4))
                phdr_flags_offset = 4
                phdr_type_fmt = endian + "I"
            else:
                # Elf32_Ehdr: e_phoff @ 28 (u32), e_phentsize @ 42 (u16),
                # e_phnum @ 44 (u16). Program header: p_type @ 0 (u32),
                # p_flags @ 24 (u32).
                f.seek(28)
                (e_phoff,) = struct.unpack(endian + "I", f.read(4))
                f.seek(42)
                e_phentsize, e_phnum = struct.unpack(endian + "HH", f.read(4))
                phdr_flags_offset = 24
                phdr_type_fmt = endian + "I"

            if e_phoff == 0 or e_phentsize == 0 or e_phnum == 0:
                return False

            for i in range(e_phnum):
                entry_off = e_phoff + i * e_phentsize
                f.seek(entry_off)
                raw_type = f.read(4)
                if len(raw_type) < 4:
                    return False
                (p_type,) = struct.unpack(phdr_type_fmt, raw_type)
                if p_type != _PT_GNU_STACK:
                    continue
                flags_off = entry_off + phdr_flags_offset
                f.seek(flags_off)
                raw_flags = f.read(4)
                if len(raw_flags) < 4:
                    return False
                (p_flags,) = struct.unpack(endian + "I", raw_flags)
                if not (p_flags & _PF_X):
                    return False
                new_flags = p_flags & ~_PF_X
                f.seek(flags_off)
                f.write(struct.pack(endian + "I", new_flags))
                return True
    except OSError:
        return False
    return False


def clear_execstack_in_tree(root: str | os.PathLike[str]) -> list[str]:
    """Walk ``root`` and sanitize every shared object underneath it.

    Returns the list of paths that were actually modified, in the order they
    were visited. Directories that do not exist raise ``FileNotFoundError``
    so CI catches a typo in the Dockerfile argument.
    """
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"clear_execstack: path does not exist: {root}")

    modified: list[str] = []
    if root_path.is_file():
        if clear_execstack(root_path):
            modified.append(str(root_path))
        return modified

    for dirpath, _, filenames in os.walk(root_path):
        for name in filenames:
            if not _is_shared_object(name):
                continue
            full = os.path.join(dirpath, name)
            if clear_execstack(full):
                modified.append(full)
    return modified


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: python clear_execstack.py <path>\n"
            "  <path> is a directory tree or a single .so file.",
            file=sys.stderr,
        )
        return 2
    target = argv[1]
    try:
        modified = clear_execstack_in_tree(target)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for path in modified:
        print(f"  [execstack] cleared PF_X on {path}")
    print(
        f"clear_execstack: sanitized {len(modified)} shared object(s) under {target}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
