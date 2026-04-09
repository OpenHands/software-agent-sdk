"""Unit tests for ``clear_execstack``.

We synthesize minimal but structurally valid ELF files in-memory so the tests
do not depend on a target toolchain being installed. Each fixture exercises a
different axis of the matrix called for by OpenHands/software-agent-sdk#2761:
32/64-bit, little-/big-endian (which stands in for amd64/arm64 byte order
shape + any future big-endian arch), and ``PT_GNU_STACK`` present / absent /
already-clean. Idempotence and the no-match no-op guarantee are asserted
explicitly.

The sanitizer only edits a single ``uint32`` inside an existing program
header, so our synthesized ELFs do not need real code or sections — just a
coherent ELF header + program header table. If the helper touches anything
outside the ``p_flags`` field, the post-call file comparisons will catch it.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from openhands.agent_server.docker.clear_execstack import (
    _is_shared_object,
    clear_execstack,
    clear_execstack_in_tree,
)


# ---------- ELF synthesis helpers ------------------------------------------------

# From <elf.h>
_PT_LOAD = 0x1
_PT_GNU_STACK = 0x6474E551
_PF_X = 0x1
_PF_W = 0x2
_PF_R = 0x4

# Elf_Ehdr sizes
_EHDR64_SIZE = 64
_EHDR32_SIZE = 52
# Phdr sizes
_PHDR64_SIZE = 56
_PHDR32_SIZE = 32


def _build_elf(
    *,
    bits: int,
    endian: str,
    phdrs: list[tuple[int, int]],
) -> bytes:
    """Build a minimal ELF file containing the given program headers.

    ``phdrs`` is a list of ``(p_type, p_flags)`` tuples. Remaining program
    header fields (offsets, sizes, alignment) are filled with zeros — the
    sanitizer only reads ``p_type`` and ``p_flags``.
    """
    assert bits in (32, 64)
    assert endian in ("<", ">")

    ei_class = 2 if bits == 64 else 1
    ei_data = 1 if endian == "<" else 2

    e_ident = bytearray(16)
    e_ident[0:4] = b"\x7fELF"
    e_ident[4] = ei_class
    e_ident[5] = ei_data
    e_ident[6] = 1  # EV_CURRENT
    # remaining bytes stay zero — valid enough for our purposes

    e_type = 3  # ET_DYN (shared object)
    e_machine = 0x3E if bits == 64 else 0x28  # EM_X86_64 / EM_ARM
    e_version = 1
    e_entry = 0
    e_flags = 0
    e_shoff = 0
    e_shentsize = 0
    e_shnum = 0
    e_shstrndx = 0

    phdr_size = _PHDR64_SIZE if bits == 64 else _PHDR32_SIZE
    ehdr_size = _EHDR64_SIZE if bits == 64 else _EHDR32_SIZE
    e_phoff = ehdr_size
    e_phentsize = phdr_size
    e_phnum = len(phdrs)
    e_ehsize = ehdr_size

    if bits == 64:
        # Elf64_Ehdr layout after e_ident:
        #   Half e_type; Half e_machine; Word e_version; Addr e_entry;
        #   Off e_phoff; Off e_shoff; Word e_flags; Half e_ehsize;
        #   Half e_phentsize; Half e_phnum; Half e_shentsize; Half e_shnum;
        #   Half e_shstrndx
        ehdr_tail = struct.pack(
            endian + "HHIQQQIHHHHHH",
            e_type,
            e_machine,
            e_version,
            e_entry,
            e_phoff,
            e_shoff,
            e_flags,
            e_ehsize,
            e_phentsize,
            e_phnum,
            e_shentsize,
            e_shnum,
            e_shstrndx,
        )
    else:
        ehdr_tail = struct.pack(
            endian + "HHIIIIIHHHHHH",
            e_type,
            e_machine,
            e_version,
            e_entry,
            e_phoff,
            e_shoff,
            e_flags,
            e_ehsize,
            e_phentsize,
            e_phnum,
            e_shentsize,
            e_shnum,
            e_shstrndx,
        )

    buf = bytearray(bytes(e_ident) + ehdr_tail)
    assert len(buf) == ehdr_size, (len(buf), ehdr_size)

    for p_type, p_flags in phdrs:
        if bits == 64:
            # Elf64_Phdr: Word p_type; Word p_flags; Off p_offset; Addr p_vaddr;
            #            Addr p_paddr; Xword p_filesz; Xword p_memsz; Xword p_align
            phdr = struct.pack(
                endian + "IIQQQQQQ",
                p_type,
                p_flags,
                0,  # p_offset
                0,  # p_vaddr
                0,  # p_paddr
                0,  # p_filesz
                0,  # p_memsz
                0,  # p_align
            )
        else:
            # Elf32_Phdr: Word p_type; Off p_offset; Addr p_vaddr; Addr p_paddr;
            #            Word p_filesz; Word p_memsz; Word p_flags; Word p_align
            phdr = struct.pack(
                endian + "IIIIIIII",
                p_type,
                0,  # p_offset
                0,  # p_vaddr
                0,  # p_paddr
                0,  # p_filesz
                0,  # p_memsz
                p_flags,
                0,  # p_align
            )
        assert len(phdr) == phdr_size
        buf.extend(phdr)

    # A few bytes of tail padding so strip/readelf don't complain. Not required
    # by the sanitizer but avoids empty-file edge cases.
    buf.extend(b"\x00" * 16)
    return bytes(buf)


def _gnu_stack_flags(data: bytes) -> int | None:
    """Read PT_GNU_STACK p_flags from a synthesized ELF, for assertions."""
    ei_class = data[4]
    ei_data = data[5]
    endian = "<" if ei_data == 1 else ">"
    if ei_class == 2:
        e_phoff = struct.unpack(endian + "Q", data[32:40])[0]
        e_phentsize, e_phnum = struct.unpack(endian + "HH", data[54:58])
        flags_in_phdr = 4
    else:
        e_phoff = struct.unpack(endian + "I", data[28:32])[0]
        e_phentsize, e_phnum = struct.unpack(endian + "HH", data[42:46])
        flags_in_phdr = 24

    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        (p_type,) = struct.unpack(endian + "I", data[off : off + 4])
        if p_type == _PT_GNU_STACK:
            flags_off = off + flags_in_phdr
            return struct.unpack(endian + "I", data[flags_off : flags_off + 4])[0]
    return None


# ---------- Parametrized matrix --------------------------------------------------

_MATRIX = [
    pytest.param(64, "<", id="elf64-le"),  # amd64
    pytest.param(64, ">", id="elf64-be"),  # e.g. ppc64be — covers 64-bit BE
    pytest.param(32, "<", id="elf32-le"),  # armhf
    pytest.param(32, ">", id="elf32-be"),  # e.g. classic MIPS — covers 32-bit BE
]


@pytest.mark.parametrize("bits,endian", _MATRIX)
def test_clears_pf_x_on_pt_gnu_stack_rwx(tmp_path: Path, bits: int, endian: str):
    elf = _build_elf(
        bits=bits,
        endian=endian,
        phdrs=[
            (_PT_LOAD, _PF_R | _PF_X),  # code segment — must NOT be touched
            (_PT_GNU_STACK, _PF_R | _PF_W | _PF_X),  # the offender
        ],
    )
    path = tmp_path / "libtest.so.1.0"
    path.write_bytes(elf)

    changed = clear_execstack(path)
    assert changed is True

    new_bytes = path.read_bytes()
    assert _gnu_stack_flags(new_bytes) == (_PF_R | _PF_W)

    # Ensure the load segment kept its PF_X — we only target PT_GNU_STACK.
    # Read the first program header's p_flags directly.
    ei_class = new_bytes[4]
    e_phoff_off = 32 if ei_class == 2 else 28
    phdr_size = _PHDR64_SIZE if ei_class == 2 else _PHDR32_SIZE
    flags_in_phdr = 4 if ei_class == 2 else 24
    if ei_class == 2:
        e_phoff = struct.unpack(endian + "Q", new_bytes[e_phoff_off : e_phoff_off + 8])[
            0
        ]
    else:
        e_phoff = struct.unpack(endian + "I", new_bytes[e_phoff_off : e_phoff_off + 4])[
            0
        ]
    load_flags_off = e_phoff + 0 * phdr_size + flags_in_phdr
    (load_flags,) = struct.unpack(
        endian + "I", new_bytes[load_flags_off : load_flags_off + 4]
    )
    assert load_flags & _PF_X, "PT_LOAD PF_X must be preserved"


@pytest.mark.parametrize("bits,endian", _MATRIX)
def test_noop_when_pt_gnu_stack_already_clean(tmp_path: Path, bits: int, endian: str):
    elf = _build_elf(
        bits=bits,
        endian=endian,
        phdrs=[(_PT_GNU_STACK, _PF_R | _PF_W)],
    )
    path = tmp_path / "libclean.so"
    path.write_bytes(elf)
    before = path.read_bytes()

    assert clear_execstack(path) is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("bits,endian", _MATRIX)
def test_noop_when_pt_gnu_stack_absent(tmp_path: Path, bits: int, endian: str):
    # Only a PT_LOAD segment; no PT_GNU_STACK at all.
    elf = _build_elf(
        bits=bits,
        endian=endian,
        phdrs=[(_PT_LOAD, _PF_R | _PF_X)],
    )
    path = tmp_path / "libnostack.so"
    path.write_bytes(elf)
    before = path.read_bytes()

    assert clear_execstack(path) is False
    assert path.read_bytes() == before


def test_idempotent(tmp_path: Path):
    elf = _build_elf(
        bits=64,
        endian="<",
        phdrs=[(_PT_GNU_STACK, _PF_R | _PF_W | _PF_X)],
    )
    path = tmp_path / "libidem.so"
    path.write_bytes(elf)

    # First pass writes. Second pass must not change anything.
    assert clear_execstack(path) is True
    after_first = path.read_bytes()
    assert clear_execstack(path) is False
    assert path.read_bytes() == after_first
    assert _gnu_stack_flags(after_first) == (_PF_R | _PF_W)


def test_walks_tree_and_reports_modified_files(tmp_path: Path):
    dirty = _build_elf(
        bits=64, endian="<", phdrs=[(_PT_GNU_STACK, _PF_R | _PF_W | _PF_X)]
    )
    clean = _build_elf(bits=64, endian="<", phdrs=[(_PT_GNU_STACK, _PF_R | _PF_W)])

    (tmp_path / "bin").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "nested").mkdir()

    (tmp_path / "lib" / "libdirty.so.1.0").write_bytes(dirty)
    (tmp_path / "lib" / "libclean.so").write_bytes(clean)
    (tmp_path / "lib" / "nested" / "libdeep.so.3").write_bytes(dirty)
    (tmp_path / "lib" / "README.md").write_text("not an elf")
    (tmp_path / "lib" / "something.sources").write_text("not a shared object")
    (tmp_path / "bin" / "binary").write_bytes(dirty)  # not .so — must be skipped

    modified = clear_execstack_in_tree(tmp_path)
    modified_names = sorted(Path(p).name for p in modified)
    assert modified_names == ["libdeep.so.3", "libdirty.so.1.0"]


def test_non_elf_file_is_skipped(tmp_path: Path):
    path = tmp_path / "libjunk.so"
    path.write_bytes(b"not an elf at all")
    assert clear_execstack(path) is False
    assert path.read_bytes() == b"not an elf at all"


def test_truncated_elf_is_skipped(tmp_path: Path):
    path = tmp_path / "libtrunc.so"
    # Valid magic + class + data, but nothing else.
    path.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 4)
    assert clear_execstack(path) is False


def test_symlink_is_skipped(tmp_path: Path):
    elf = _build_elf(
        bits=64, endian="<", phdrs=[(_PT_GNU_STACK, _PF_R | _PF_W | _PF_X)]
    )
    real = tmp_path / "libreal.so.1.0"
    real.write_bytes(elf)
    link = tmp_path / "libreal.so"
    link.symlink_to(real.name)

    # Calling on the symlink must not modify anything…
    assert clear_execstack(link) is False
    # …and calling on the real file must still work.
    assert clear_execstack(real) is True
    assert _gnu_stack_flags(real.read_bytes()) == (_PF_R | _PF_W)


def test_clear_execstack_in_tree_rejects_missing_path(tmp_path: Path):
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        clear_execstack_in_tree(missing)


def test_clear_execstack_in_tree_accepts_single_file(tmp_path: Path):
    elf = _build_elf(
        bits=64, endian="<", phdrs=[(_PT_GNU_STACK, _PF_R | _PF_W | _PF_X)]
    )
    path = tmp_path / "libsolo.so"
    path.write_bytes(elf)
    modified = clear_execstack_in_tree(path)
    assert modified == [str(path)]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("libfoo.so", True),
        ("libfoo.so.1", True),
        ("libfoo.so.1.2.3", True),
        ("libpython3.13.so.1.0", True),
        ("foo.sources", False),
        ("notes.so.txt", False),
        ("README.md", False),
        ("pyconfig.h", False),
    ],
)
def test_is_shared_object(name: str, expected: bool):
    assert _is_shared_object(name) is expected


def test_cli_entrypoint_reports_modifications(tmp_path: Path, capsys):
    from openhands.agent_server.docker.clear_execstack import _main

    elf = _build_elf(
        bits=64, endian="<", phdrs=[(_PT_GNU_STACK, _PF_R | _PF_W | _PF_X)]
    )
    (tmp_path / "libcli.so").write_bytes(elf)

    rc = _main(["clear_execstack.py", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "libcli.so" in out
    assert "sanitized 1 shared object" in out


def test_cli_entrypoint_usage_error(capsys):
    from openhands.agent_server.docker.clear_execstack import _main

    rc = _main(["clear_execstack.py"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_cli_entrypoint_missing_path(tmp_path: Path, capsys):
    from openhands.agent_server.docker.clear_execstack import _main

    rc = _main(["clear_execstack.py", str(tmp_path / "nope")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "does not exist" in err
