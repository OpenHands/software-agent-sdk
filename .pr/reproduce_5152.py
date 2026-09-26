#!/usr/bin/env python3
"""Issue reproducer for #5152.

Demonstrates that before the fix, the agent-server reported browser tools
as usable (is_usable() == True) while create() silently returned no tools
due to the mcp 2.x / browser-use 0.11.x incompatibility.

After the fix:
  - _probe_mcp_import() is called by both is_usable() and create().
  - If the MCP server module can't import, is_usable() returns False and
    create() returns [] immediately with a clear warning.

Run in WSL:
  .venv/bin/python .pr/reproduce_5152.py
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


class _BrokenMcpServerModule(types.ModuleType):
    """Simulates browser_use/mcp/server.py failing with mcp 2.x.

    Python converts AttributeError from __getattr__ into ImportError when
    executing 'from X import Y', which is the error the probe catches.
    """

    def __getattr__(self, name: str) -> object:
        raise AttributeError(
            f"'Server' object has no attribute '{name}' "
            "(simulating browser-use 0.11.x + mcp 2.x incompatibility)"
        )


@contextmanager
def _broken_mcp_env():
    """Context manager that replaces the server module with a broken one."""
    broken = _BrokenMcpServerModule("openhands.tools.browser_use.server")
    with patch.dict("sys.modules", {"openhands.tools.browser_use.server": broken}):
        yield


def run_broken_scenario() -> None:
    from openhands.tools.browser_use.definition import BrowserToolSet
    from openhands.tools.browser_use.impl import BrowserToolExecutor

    BrowserToolExecutor.check_chromium_available.cache_clear()
    BrowserToolSet._mcp_import_ok = None

    print("\n" + "=" * 60)
    print("Scenario: BROKEN — Chromium installed, MCP server fails to import")
    print("=" * 60)

    with (
        patch.object(
            BrowserToolExecutor,
            "check_chromium_available",
            return_value="/usr/bin/chromium",
        ),
        _broken_mcp_env(),
    ):
        is_usable = BrowserToolSet.is_usable()
        conv_state = MagicMock()
        tools = BrowserToolSet.create(conv_state)

    print(f"  is_usable()  → {is_usable}")
    print(f"  create() len → {len(tools)}")
    assert not is_usable, "FAIL: is_usable() should be False when MCP import fails"
    assert tools == [], "FAIL: create() should return [] when MCP import fails"
    print("  ✓ Both correctly return False/[] — no phantom tool injection")


def run_healthy_scenario() -> None:
    from openhands.tools.browser_use.definition import BrowserToolSet
    from openhands.tools.browser_use.impl import BrowserToolExecutor

    BrowserToolExecutor.check_chromium_available.cache_clear()
    BrowserToolSet._mcp_import_ok = None

    print("\n" + "=" * 60)
    print("Scenario: HEALTHY — Chromium installed, MCP server importable")
    print("=" * 60)

    with (
        patch.object(
            BrowserToolExecutor,
            "check_chromium_available",
            return_value="/usr/bin/chromium",
        ),
        patch.object(BrowserToolSet, "_probe_mcp_import", return_value=True),
    ):
        is_usable = BrowserToolSet.is_usable()

    print(f"  is_usable()  → {is_usable}")
    assert is_usable, "FAIL: is_usable() should be True in healthy env"
    print("  ✓ is_usable() correctly returns True in healthy env")


def main() -> None:
    print("Reproducer for issue #5152")
    print("browser-use 0.11.x + mcp 2.x silent disappearance")

    run_healthy_scenario()
    run_broken_scenario()

    print("\n" + "=" * 60)
    print("All scenarios passed ✓")
    print("The fix prevents silent browser-tool disappearance.")


if __name__ == "__main__":
    main()
