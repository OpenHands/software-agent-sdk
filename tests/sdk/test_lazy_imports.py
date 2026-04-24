from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterable


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        check=True,
        env=env,
        text=True,
    )


def _loaded_modules(stdout: str) -> set[str]:
    return set(json.loads(stdout))


def _assert_present(modules: set[str], names: Iterable[str]) -> None:
    for name in names:
        assert name in modules


def _assert_absent(modules: set[str], names: Iterable[str]) -> None:
    for name in names:
        assert name not in modules


def test_import_openhands_sdk_keeps_heavy_modules_lazy() -> None:
    result = _run_python(
        "import json, sys; import openhands.sdk; "
        "print(json.dumps(sorted(name for name in sys.modules "
        'if name.startswith(("openhands.sdk", "litellm", "fastmcp", "lmnr")))))'
    )

    modules = _loaded_modules(result.stdout)

    _assert_present(
        modules,
        {
            "openhands.sdk",
            "openhands.sdk._lazy_imports",
            "openhands.sdk.banner",
        },
    )
    _assert_absent(
        modules,
        {
            "litellm",
            "fastmcp",
            "lmnr",
            "openhands.sdk.agent",
            "openhands.sdk.agent.agent",
            "openhands.sdk.conversation",
            "openhands.sdk.event",
            "openhands.sdk.llm",
            "openhands.sdk.logger",
            "openhands.sdk.mcp",
            "openhands.sdk.plugin",
            "openhands.sdk.skills",
            "openhands.sdk.tool",
        },
    )


def test_importing_lightweight_llm_exports_does_not_import_llm_runtime() -> None:
    result = _run_python(
        "import json, sys; from openhands.sdk.llm import Message, TextContent; "
        "print(json.dumps(sorted(name for name in sys.modules "
        'if name.startswith(("openhands.sdk.llm", "litellm")))))'
    )

    modules = _loaded_modules(result.stdout)

    _assert_present(
        modules,
        {
            "openhands.sdk.llm",
            "openhands.sdk.llm.message",
        },
    )
    _assert_absent(
        modules,
        {
            "openhands.sdk.llm.llm",
        },
    )
