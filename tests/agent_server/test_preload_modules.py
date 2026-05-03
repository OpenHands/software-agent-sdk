"""Tests for the --import-modules preloading helper."""

import logging
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from openhands.agent_server.__main__ import preload_modules


class TestPreloadModules:
    def test_none_is_noop(self):
        with patch(
            "openhands.agent_server.__main__.importlib.import_module"
        ) as mock_import:
            preload_modules(None)
        mock_import.assert_not_called()

    def test_empty_string_is_noop(self):
        with patch(
            "openhands.agent_server.__main__.importlib.import_module"
        ) as mock_import:
            preload_modules("")
        mock_import.assert_not_called()

    def test_single_module(self):
        with patch(
            "openhands.agent_server.__main__.importlib.import_module"
        ) as mock_import:
            preload_modules("myapp.tools")
        mock_import.assert_called_once_with("myapp.tools")

    def test_comma_separated_strips_whitespace(self):
        with patch(
            "openhands.agent_server.__main__.importlib.import_module"
        ) as mock_import:
            preload_modules(" myapp.tools , myapp.plugins ")
        assert [c.args[0] for c in mock_import.call_args_list] == [
            "myapp.tools",
            "myapp.plugins",
        ]

    def test_empty_segments_skipped(self):
        with patch(
            "openhands.agent_server.__main__.importlib.import_module"
        ) as mock_import:
            preload_modules("myapp.tools,,myapp.plugins, ")
        assert [c.args[0] for c in mock_import.call_args_list] == [
            "myapp.tools",
            "myapp.plugins",
        ]

    def test_missing_module_raises(self):
        # Follow project convention: don't swallow import errors.
        with pytest.raises(ModuleNotFoundError):
            preload_modules("definitely_not_a_real_module_xyz_2771")

    @pytest.fixture
    def fake_tool_module(self, tmp_path, monkeypatch):
        """Create an on-disk module whose top-level body has an observable
        side effect (analogous to a `register_tool(...)` call)."""
        pkg_name = "preload_modules_test_pkg"
        pkg = tmp_path / pkg_name
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "my_tool.py").write_text(
            textwrap.dedent(
                """\
                REGISTRY = []
                REGISTRY.append("MyCustomTool")
                """
            )
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        qualname = f"{pkg_name}.my_tool"
        sys.modules.pop(pkg_name, None)
        sys.modules.pop(qualname, None)
        yield qualname
        sys.modules.pop(pkg_name, None)
        sys.modules.pop(qualname, None)

    def test_module_side_effects_execute(self, fake_tool_module):
        """With the flag: side effects land before conversations are served —
        the race this flag exists to fix."""
        preload_modules(fake_tool_module)

        imported = sys.modules[fake_tool_module]
        assert imported.REGISTRY == ["MyCustomTool"]

    def test_module_not_imported_without_flag(self, fake_tool_module):
        """Contract companion: if `preload_modules` is not called (i.e. the
        operator forgot `--import-modules`), the module stays unimported and
        its `register_tool`-style side effects never run. This is exactly
        the broken state the CLI flag exists to prevent."""
        preload_modules(None)

        assert fake_tool_module not in sys.modules

    def test_import_error_is_logged_before_raising(self, caplog):
        """Import failures should log the module name and error for
        operator diagnostics before re-raising."""
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ModuleNotFoundError):
                preload_modules("no_such_module_xyz_2771")

        assert any(
            "no_such_module_xyz_2771" in r.message and "--import-modules" in r.message
            for r in caplog.records
        )


class TestMainCheckBrowserOrdering:
    """Verify --check-browser runs independently of --import-modules."""

    def test_check_browser_exits_before_preload(self):
        """--check-browser should short-circuit before preload_modules
        runs, so a broken user module cannot mask the browser check."""
        mock_result = MagicMock()
        mock_result.is_error = False

        mock_executor = MagicMock()
        mock_executor.return_value = mock_result

        with (
            patch("sys.argv", ["prog", "--check-browser", "--import-modules", "boom"]),
            patch("openhands.tools.preset.default.register_default_tools"),
            patch(
                "openhands.tools.browser_use.impl.BrowserToolExecutor",
                return_value=mock_executor,
            ),
            patch("openhands.agent_server.__main__.preload_modules") as mock_preload,
        ):
            from openhands.agent_server.__main__ import main

            with pytest.raises(SystemExit) as exc_info:
                main()

            # Browser check succeeded → exit 0
            assert exc_info.value.code == 0
            # preload_modules must NOT have been called
            mock_preload.assert_not_called()
