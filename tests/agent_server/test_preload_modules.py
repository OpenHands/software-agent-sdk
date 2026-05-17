"""Tests for the --import-modules preloading and --extra-python-path helpers."""

import importlib
import logging
import os
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from openhands.agent_server.__main__ import (
    _EXTRA_PYTHON_PATH_ENV,
    _get_internal_server_url,
    extend_python_path,
    preload_modules,
)


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


class TestExtendPythonPath:
    """Tests for extend_python_path() — the enabler for custom tool imports
    in both source and binary (PyInstaller) agent-server builds."""

    def test_none_and_no_env_is_noop(self, monkeypatch):
        monkeypatch.delenv(_EXTRA_PYTHON_PATH_ENV, raising=False)
        original = sys.path.copy()
        extend_python_path(None)
        assert sys.path == original

    def test_empty_string_and_no_env_is_noop(self, monkeypatch):
        monkeypatch.delenv(_EXTRA_PYTHON_PATH_ENV, raising=False)
        original = sys.path.copy()
        extend_python_path("")
        assert sys.path == original

    def test_adds_directory_from_cli_arg(self, tmp_path, monkeypatch):
        monkeypatch.delenv(_EXTRA_PYTHON_PATH_ENV, raising=False)
        d = tmp_path / "custom_tools"
        d.mkdir()
        extend_python_path(str(d))
        assert str(d) in sys.path
        sys.path.remove(str(d))

    def test_adds_directory_from_env_var(self, tmp_path, monkeypatch):
        d = tmp_path / "env_tools"
        d.mkdir()
        monkeypatch.setenv(_EXTRA_PYTHON_PATH_ENV, str(d))
        extend_python_path(None)
        assert str(d) in sys.path
        sys.path.remove(str(d))

    def test_merges_cli_and_env(self, tmp_path, monkeypatch):
        d1 = tmp_path / "cli_tools"
        d2 = tmp_path / "env_tools"
        d1.mkdir()
        d2.mkdir()
        monkeypatch.setenv(_EXTRA_PYTHON_PATH_ENV, str(d2))
        extend_python_path(str(d1))
        assert str(d1) in sys.path
        assert str(d2) in sys.path
        sys.path.remove(str(d1))
        sys.path.remove(str(d2))

    def test_skips_nonexistent_dir_with_warning(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv(_EXTRA_PYTHON_PATH_ENV, raising=False)
        bogus = str(tmp_path / "does_not_exist")
        with caplog.at_level(logging.WARNING):
            extend_python_path(bogus)
        assert bogus not in sys.path
        assert any("non-existent" in r.message for r in caplog.records)

    def test_deduplicates(self, tmp_path, monkeypatch):
        monkeypatch.delenv(_EXTRA_PYTHON_PATH_ENV, raising=False)
        d = tmp_path / "dup_tools"
        d.mkdir()
        extend_python_path(f"{d}{os.pathsep}{d}")
        count = sys.path.count(str(d))
        assert count == 1
        sys.path.remove(str(d))

    def test_skips_already_on_sys_path(self, tmp_path, monkeypatch):
        monkeypatch.delenv(_EXTRA_PYTHON_PATH_ENV, raising=False)
        d = tmp_path / "already_there"
        d.mkdir()
        abs_d = str(d.resolve())
        sys.path.insert(0, abs_d)
        before_count = sys.path.count(abs_d)
        extend_python_path(abs_d)
        assert sys.path.count(abs_d) == before_count
        sys.path.remove(abs_d)

    def test_multiple_dirs_via_pathsep(self, tmp_path, monkeypatch):
        monkeypatch.delenv(_EXTRA_PYTHON_PATH_ENV, raising=False)
        d1 = tmp_path / "tools_a"
        d2 = tmp_path / "tools_b"
        d1.mkdir()
        d2.mkdir()
        extend_python_path(f"{d1}{os.pathsep}{d2}")
        assert str(d1) in sys.path
        assert str(d2) in sys.path
        sys.path.remove(str(d1))
        sys.path.remove(str(d2))

    def test_enables_import_of_external_module(self, tmp_path, monkeypatch):
        """End-to-end: extend_python_path + importlib.import_module works
        for a .py file placed in the extra directory."""
        monkeypatch.delenv(_EXTRA_PYTHON_PATH_ENV, raising=False)
        d = tmp_path / "ext_tools"
        d.mkdir()
        mod_name = "ext_test_tool_abc123"
        (d / f"{mod_name}.py").write_text("REGISTERED = True\n")

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod_name)

        extend_python_path(str(d))
        try:
            mod = importlib.import_module(mod_name)
            assert mod.REGISTERED is True
        finally:
            sys.path.remove(str(d))
            sys.modules.pop(mod_name, None)

    def test_enables_preload_modules_integration(self, tmp_path, monkeypatch):
        """Confirm the intended workflow: extend_python_path() then
        preload_modules() successfully imports an external tool module."""
        monkeypatch.delenv(_EXTRA_PYTHON_PATH_ENV, raising=False)
        d = tmp_path / "integration_tools"
        d.mkdir()
        mod_name = "integration_test_tool_xyz789"
        (d / f"{mod_name}.py").write_text(
            textwrap.dedent("""\
                TOOL_REGISTRY = []
                TOOL_REGISTRY.append("IntegrationTestTool")
            """)
        )

        extend_python_path(str(d))
        try:
            preload_modules(mod_name)
            imported = sys.modules[mod_name]
            assert imported.TOOL_REGISTRY == ["IntegrationTestTool"]
        finally:
            sys.path.remove(str(d))
            sys.modules.pop(mod_name, None)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]"])
def test_get_internal_server_url_rewrites_wildcard_host(host):
    assert _get_internal_server_url(host, 4321) == "http://127.0.0.1:4321"


def test_get_internal_server_url_preserves_explicit_host():
    assert _get_internal_server_url("localhost", 4321) == "http://localhost:4321"


def test_get_internal_server_url_brackets_ipv6_host():
    assert _get_internal_server_url("fe80::1", 4321) == "http://[fe80::1]:4321"


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

    def test_main_sets_internal_server_url(self, monkeypatch):
        monkeypatch.delenv("OH_INTERNAL_SERVER_URL", raising=False)

        with (
            patch("sys.argv", ["prog", "--host", "0.0.0.0", "--port", "4321"]),
            patch("openhands.agent_server.__main__.preload_modules"),
            patch("openhands.agent_server.__main__.LoggingServer") as mock_server_cls,
        ):
            mock_server_cls.return_value.run.side_effect = SystemExit(0)

            from openhands.agent_server.__main__ import main

            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert os.environ["OH_INTERNAL_SERVER_URL"] == "http://127.0.0.1:4321"
