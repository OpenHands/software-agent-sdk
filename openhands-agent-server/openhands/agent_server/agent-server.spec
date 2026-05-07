# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for OpenHands Agent Server with PEP 420 (implicit namespace) layout.
"""

from pathlib import Path
import os
import site
from PyInstaller.utils.hooks import (
    collect_all,
    collect_submodules,
    collect_data_files,
    copy_metadata,
)

# Optional Vertex AI bundle. google-cloud-aiplatform is an opt-in extra
# (`openhands-sdk[vertex]`) and is NOT bundled in the default agent-server
# build. To produce a binary that supports `vertex_ai/*` partner models
# (MiniMax, Qwen, Kimi MaaS endpoints):
#
#   - Docker:    docker build --build-arg ENABLE_VERTEX=1 ...
#   - From src:  uv sync --frozen --dev --no-editable --extra boto3 --extra vertex
#                uv run pyinstaller .../agent-server.spec
#
# When `vertexai` is importable we use collect_all(...) for the Vertex SDK
# and its google.cloud.* namespace siblings: the imports happen inside
# function bodies AND traverse PEP-420 google.cloud namespace packages, so
# collect_submodules alone misses everything below the namespace root.
# collect_all walks the actual installed dirs.
import importlib.util as _vertex_importlib_util

_VERTEX_AVAILABLE = _vertex_importlib_util.find_spec("vertexai") is not None

_vertex_pkgs = (
    "vertexai",
    "google.cloud.aiplatform",
    "google.cloud.aiplatform_v1",
    "google.cloud.aiplatform_v1beta1",
    "google.cloud.bigquery",
    "google.cloud.storage",
    "google.cloud.resourcemanager",
    "google.api_core",
    "google.auth",
    "google.rpc",
    "google.genai",
    "proto",
    "grpc_status",
)
_vertex_datas = []
_vertex_binaries = []
_vertex_hiddenimports = []
if _VERTEX_AVAILABLE:
    for _pkg in _vertex_pkgs:
        _d, _b, _h = collect_all(_pkg)
        _vertex_datas += _d
        _vertex_binaries += _b
        _vertex_hiddenimports += _h
    # google.rpc.status_pb2 is a gRPC proto stub imported dynamically; only pin
    # it when the SDK is actually present.
    _vertex_hiddenimports.append("google.rpc.status_pb2")
else:
    print(
        "[agent-server.spec] vertexai not installed; "
        "skipping Vertex AI bundle collection. "
        "Install openhands-sdk[vertex] before building to include it."
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
    binaries=[
        # Vertex AI SDK binaries (collected via collect_all above)
        *_vertex_binaries,
    ],
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

        # Vertex AI SDK datas (collected via collect_all above)
        *_vertex_datas,
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

        # Vertex AI SDK hidden imports (collected via collect_all above; empty
        # if openhands-sdk[vertex] is not installed in the build env).
        *_vertex_hiddenimports,

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
