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

# Collect everything (submodules + datas + binaries + metadata) for the
# Vertex AI SDK so LiteLLM's vertex_ai_partner_models handler can route to
# Vertex AI Model Garden MaaS endpoints (MiniMax, Qwen, Kimi). The imports
# happen inside function bodies AND traverse PEP-420 google.cloud namespace
# packages, so collect_submodules alone misses everything below the namespace
# root. collect_all walks the actual installed dirs.
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
for _pkg in _vertex_pkgs:
    _d, _b, _h = collect_all(_pkg)
    _vertex_datas += _d
    _vertex_binaries += _b
    _vertex_hiddenimports += _h

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

        # Vertex AI SDK hidden imports (collected via collect_all above)
        *_vertex_hiddenimports,
        # google.rpc.status_pb2 is a gRPC proto stub imported dynamically by
        # the gRPC channel layer; pin it explicitly in case namespace traversal
        # misses it.
        "google.rpc.status_pb2",

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
