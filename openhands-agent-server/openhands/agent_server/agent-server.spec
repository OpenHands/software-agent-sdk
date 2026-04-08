# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for OpenHands Agent Server with PEP 420 (implicit namespace) layout.
"""

from pathlib import Path
import os
import site
from PyInstaller.utils.hooks import (
    collect_submodules,
    collect_data_files,
    copy_metadata,
)

# Get the project root directory (current working directory when running PyInstaller)
project_root = Path.cwd()
# Namespace roots must be in pathex so PyInstaller can find 'openhands/...'
PATHEX = [
    project_root / "openhands-agent-server",
    project_root / "openhands-sdk",
    project_root / "openhands-tools",
    project_root / "openhands-workspace",
]

# Entry script for the agent server package (namespace: openhands/agent_server/__main__.py)
ENTRY = str(project_root / "openhands-agent-server" / "openhands" / "agent_server" / "__main__.py")

# Find fakeredis package location to get commands.json with correct path
def get_fakeredis_data():
    """Get fakeredis data files with correct directory structure.
    
    fakeredis/model/_command_info.py uses Path(__file__).parent.parent / "commands.json"
    which means it expects commands.json to be at fakeredis/commands.json when accessed
    from fakeredis/model/. We need to ensure the model/ subdirectory exists in the bundle.
    """
    import fakeredis
    fakeredis_dir = Path(fakeredis.__file__).parent
    commands_json = fakeredis_dir / "commands.json"
    
    data_files = []
    if commands_json.exists():
        # Add commands.json to fakeredis/ directory
        data_files.append((str(commands_json), "fakeredis"))
    
    # Add a placeholder file to create the model/ subdirectory structure
    # This ensures Path(__file__).parent.parent works correctly for model/ modules
    model_dir = fakeredis_dir / "model"
    if model_dir.exists():
        # Find any .py file in model/ to include (PyInstaller needs at least one file)
        for py_file in model_dir.glob("*.py"):
            # We don't actually need the .py files (they're compiled), but we need
            # the __init__.py to create the directory structure
            if py_file.name == "__init__.py":
                data_files.append((str(py_file), "fakeredis/model"))
                break
    
    return data_files

a = Analysis(
    [ENTRY],
    pathex=PATHEX,
    binaries=[],
    datas=[
        # Third-party packages that ship data
        *collect_data_files("tiktoken"),
        *collect_data_files("tiktoken_ext"),
        *collect_data_files("litellm"),
        *collect_data_files("fastmcp"),
        *collect_data_files("mcp"),
        *collect_data_files("fakeredis"),  # Required for commands.json used by fakeredis ACL
        *get_fakeredis_data(),  # Ensure fakeredis/model/ directory structure exists

        # OpenHands SDK prompt templates (adjusted for shallow namespace layout)
        *collect_data_files("openhands.sdk.agent", includes=["prompts/*.j2"]),
        *collect_data_files("openhands.sdk.context.condenser", includes=["prompts/*.j2"]),
        *collect_data_files("openhands.sdk.context.prompts", includes=["templates/*.j2"]),

        # OpenHands Tools templates
        *collect_data_files("openhands.tools.delegate", includes=["templates/*.j2"]),

        # OpenHands Tools browser recording JS files
        *collect_data_files("openhands.tools.browser_use", includes=["js/*.js"]),

        # Package metadata for importlib.metadata
        *copy_metadata("fastmcp"),
        *copy_metadata("litellm"),
    ],
    hiddenimports=[
        # Pull all OpenHands modules from the namespace (PEP 420 safe once pathex is correct)
        *collect_submodules("openhands.sdk"),
        *collect_submodules("openhands.tools"),
        *collect_submodules("openhands.workspace"),
        *collect_submodules("openhands.agent_server"),

        # Third-party dynamic imports
        *collect_submodules("tiktoken"),
        *collect_submodules("tiktoken_ext"),
        *collect_submodules("litellm"),
        *collect_submodules("fastmcp"),
        *collect_submodules("fakeredis"),
        *collect_submodules("lupa"),  # Required for fakeredis[lua] Lua scripting support

        # mcp subpackages used at runtime (avoid CLI)
        "mcp.types",
        "mcp.client",
        "mcp.server",
        "mcp.shared",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim size
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
        "notebook",
        # Exclude mcp CLI parts that pull in typer/extra deps
        "mcp.cli",
        "mcp.cli.cli",
    ],
    noarchive=False,
    # IMPORTANT: don't use optimize=2 (-OO); it strips docstrings needed by parsers (e.g., PLY/bashlex)
    optimize=0,
)

# Remove system libraries that must come from the runtime image, not the builder.
# The PyInstaller binary extracts to /tmp/_MEI*/ and sets LD_LIBRARY_PATH there.
# Child processes (e.g. tmux) inherit this and pick up the bundled libs instead
# of the runtime's system libs, causing version mismatches:
#  - libgcc_s.so: builder may lack GCC_14.0 symbols the runtime expects
#  - libtinfo/libncurses: builder's ncurses is older than runtime's tmux expects
_EXCLUDE_LIB_PREFIXES = ('libgcc_s.so', 'libtinfo.so', 'libncurses')
a.binaries = [x for x in a.binaries if not x[0].startswith(_EXCLUDE_LIB_PREFIXES)]

# ---------------------------------------------------------------------------
# Fix executable stack flags on bundled shared libraries.
#
# python-build-standalone's libpython3.13.so.1.0 (used by uv-managed Python)
# is built with PT_GNU_STACK PF_X.  glibc >= 2.41-12+deb13u2 (Debian Trixie,
# used in the nikolaik runtime image) tightened NX-stack enforcement and the
# dynamic linker now rejects such libraries with EINVAL.  sysbox-runc's
# seccomp policy also blocks the mprotect(PROT_EXEC) fallback.
#
# We reuse the same clear_execstack helper the Dockerfile builder stage
# applies to /agent-server/uv-managed-python, now as a post-Analysis hook
# against PyInstaller's collected binaries — so the one-file archive ships
# clean .so files that load under strict NX.
#
# We copy each affected .so into a temp directory (so we never mutate the
# files on disk that Analysis pointed at) and rewrite the binaries list to
# use the sanitized copy. PyInstaller's subsequent strip preserves program
# headers, so the cleared flag survives into the final binary.
# See OpenHands/software-agent-sdk#2761 (and #2574 for the original spec-only
# version this supersedes).
# ---------------------------------------------------------------------------
import importlib.util as _clear_execstack_importer
import shutil as _nxfix_shutil
import tempfile as _nxfix_tempfile

_clear_execstack_path = str(
    project_root
    / "openhands-agent-server"
    / "openhands"
    / "agent_server"
    / "docker"
    / "clear_execstack.py"
)
_spec = _clear_execstack_importer.spec_from_file_location(
    "agent_server_clear_execstack", _clear_execstack_path
)
assert _spec is not None and _spec.loader is not None, (
    f"clear_execstack helper not found at {_clear_execstack_path}"
)
_clear_execstack_mod = _clear_execstack_importer.module_from_spec(_spec)
_spec.loader.exec_module(_clear_execstack_mod)

_nxfix_tmpdir = _nxfix_tempfile.mkdtemp(prefix='pyinstaller_nxfix_')
_fixed_binaries = []
for _name, _path, _typecode in a.binaries:
    if '.so' in _name:
        _tmp_path = os.path.join(_nxfix_tmpdir, _name.replace(os.sep, '_'))
        _nxfix_shutil.copy2(_path, _tmp_path)
        if _clear_execstack_mod.clear_execstack(_tmp_path):
            print(f'  [NX-fix] Cleared executable stack: {_name}')
            _fixed_binaries.append((_name, _tmp_path, _typecode))
            continue
        os.unlink(_tmp_path)
    _fixed_binaries.append((_name, _path, _typecode))
a.binaries = _fixed_binaries

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="openhands-agent-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
