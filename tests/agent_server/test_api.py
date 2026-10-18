"""Tests for the agent server API functionality."""

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import libtmux
import pytest
from fastapi.testclient import TestClient

from openhands.agent_server import (
    config as config_module,
    conversation_service as conversation_service_module,
)
from openhands.agent_server.api import (
    _cleanup_stale_tmux_sessions,
    _default_server_tmux_tmpdir,
    _ensure_server_tmux_tmpdir,
    _get_root_path,
    api_lifespan,
    create_app,
)
from openhands.agent_server.config import Config
from openhands.tools.terminal.terminal.tmux_pane_pool import TmuxPanePool


@pytest.fixture(autouse=True)
def clear_web_url_env(monkeypatch):
    monkeypatch.delenv("OH_WEB_URL", raising=False)
    monkeypatch.delenv("RUNTIME_URL", raising=False)
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)


@pytest.fixture
def short_tmp_path(tmp_path):
    """A directory shallow enough for tmux sockets; pytest's tmp_path is not."""
    if os.name != "posix":
        yield tmp_path
        return
    with tempfile.TemporaryDirectory(prefix="oh-", dir="/tmp") as path:
        yield Path(path)


@pytest.fixture
def deep_tmp_path(tmp_path):
    """A directory too deep for tmux sockets, like a repo-local CI state dir."""
    path = tmp_path / ("d" * 64)
    path.mkdir()
    return path


def test_default_server_tmux_tmpdir_uses_current_pid(monkeypatch):
    # Path selection does not require a real directory. Avoid adding a random
    # fixture directory above the production per-process name.
    monkeypatch.setattr(
        "openhands.agent_server.api.tempfile.gettempdir", lambda: "/tmp"
    )

    assert _default_server_tmux_tmpdir() == (
        Path("/tmp") / f"openhands-agent-server-{os.getpid()}"
    )


@pytest.mark.skipif(os.name != "posix", reason="tmux requires Unix")
def test_default_server_tmux_tmpdir_avoids_deep_tempdir(deep_tmp_path, monkeypatch):
    monkeypatch.setattr(
        "openhands.agent_server.api.tempfile.gettempdir", lambda: str(deep_tmp_path)
    )

    assert _default_server_tmux_tmpdir() == (
        Path("/tmp") / f"openhands-agent-server-{os.getpid()}"
    )


@pytest.mark.skipif(os.name != "posix", reason="Unix socket paths")
@pytest.mark.parametrize(
    "socket_limit, expected_root",
    [(103, "/tmp"), (107, "/tmp/oh-abcdefgh")],
    ids=["macos", "linux"],
)
def test_default_server_tmux_tmpdir_counts_canonical_socket_bytes(
    socket_limit, expected_root, monkeypatch
):
    # Model macOS's /tmp -> /private/tmp, with a fixed UID/PID. The original
    # test fixture produced a 105-byte socket path; the fallback is 93.
    # Replace the module's os reference, not global os.getpid/getuid.
    monkeypatch.setattr(
        "openhands.agent_server.api.os",
        SimpleNamespace(
            name="posix",
            getpid=lambda: 12345,
            getuid=lambda: 501,
            fsencode=os.fsencode,
            path=SimpleNamespace(realpath=lambda path: f"/private{path}"),
        ),
    )
    monkeypatch.setattr(
        "openhands.agent_server.api._MAX_TMUX_SOCKET_PATH", socket_limit
    )
    monkeypatch.setattr(
        "openhands.agent_server.api.tempfile.gettempdir", lambda: "/tmp/oh-abcdefgh"
    )

    assert _default_server_tmux_tmpdir() == (
        Path(expected_root) / "openhands-agent-server-12345"
    )


def test_ensure_server_tmux_tmpdir_creates_default_dir(short_tmp_path, monkeypatch):
    # Test directory creation independently of path selection above.
    expected = short_tmp_path / "tmux"
    monkeypatch.setattr(
        "openhands.agent_server.api._default_server_tmux_tmpdir", lambda: expected
    )

    tmux_tmpdir, was_defaulted, created = _ensure_server_tmux_tmpdir()

    assert was_defaulted is True
    assert created is True
    assert tmux_tmpdir == expected
    assert tmux_tmpdir.is_dir()
    assert os.environ["TMUX_TMPDIR"] == str(tmux_tmpdir)


def test_ensure_server_tmux_tmpdir_respects_existing_env(short_tmp_path, monkeypatch):
    existing = short_tmp_path / "custom-tmux"
    monkeypatch.setenv("TMUX_TMPDIR", str(existing))

    tmux_tmpdir, was_defaulted, created = _ensure_server_tmux_tmpdir()

    assert was_defaulted is False
    assert created is False
    assert tmux_tmpdir == existing
    assert not existing.exists()


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for tmux output"
        time.sleep(0.05)


_ORPHAN_SOCKET_SCRIPT = """
import tempfile
from openhands.tools.terminal.terminal.tmux_pane_pool import TmuxPanePool

pool = TmuxPanePool(tempfile.mkdtemp(prefix="oh-orphan-workdir-"))
pool.initialize()
pool.checkout()
print(pool._server.socket_name)
"""


@pytest.mark.skipif(os.name != "posix", reason="tmux requires Unix")
def test_ensure_server_tmux_tmpdir_replaces_too_deep_env(
    tmp_path, deep_tmp_path, short_tmp_path, monkeypatch
):
    monkeypatch.setenv("TMUX_TMPDIR", str(deep_tmp_path))
    # Use a genuinely short final directory for the real tmux integration test.
    expected = short_tmp_path / "tmux"
    monkeypatch.setattr(
        "openhands.agent_server.api._default_server_tmux_tmpdir", lambda: expected
    )

    tmux_tmpdir, was_defaulted, created = _ensure_server_tmux_tmpdir()

    assert was_defaulted is True
    assert tmux_tmpdir == expected
    assert os.environ["TMUX_TMPDIR"] == str(tmux_tmpdir)
    pool = TmuxPanePool(str(tmp_path))
    try:
        pool.initialize()
    finally:
        pool.close()


@pytest.mark.skipif(os.name != "posix", reason="tmux requires Unix")
def test_cleanup_stale_tmux_sessions_spares_live_sibling_socket(monkeypatch):
    """Startup cleanup must not kill a live sibling agent-server's sessions.

    Regression for #5581: with a shared, externally supplied ``TMUX_TMPDIR``
    (as on an Agent Canvas host), ``_cleanup_stale_tmux_sessions()`` used to
    glob every ``openhands-<32 hex>`` socket and kill all of its sessions,
    silently destroying the terminals of every other running agent-server.
    A socket whose owner is gone must still be reclaimed.
    """
    with tempfile.TemporaryDirectory(
        prefix="oh-shared-tmux-", dir="/tmp"
    ) as socket_dir:
        monkeypatch.setenv("TMUX_TMPDIR", socket_dir)

        # Conversation A: a live pool whose long-running command must survive.
        pool_a = TmuxPanePool(tempfile.mkdtemp(prefix="oh-conversation-a-"))
        pool_a.initialize()
        terminal_a = pool_a.checkout()

        # Legacy and non-OpenHands sockets share the directory.
        legacy = libtmux.Server(socket_name="openhands")
        unrelated = libtmux.Server(socket_name="unrelated")
        legacy.new_session(session_name="test")
        unrelated.new_session(session_name="test")
        try:
            terminal_a.send_keys("printf 'A_STILL_ALIVE\\n'")
            _wait_for(lambda: "A_STILL_ALIVE" in terminal_a.read_screen())

            # An orphaned socket whose owner process has exited (a crashed
            # sibling) is created out-of-process, then its owner dies.
            env = {**os.environ, "TMUX_TMPDIR": socket_dir}
            orphan = subprocess.run(
                [sys.executable, "-c", _ORPHAN_SOCKET_SCRIPT],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            orphan_socket = orphan.stdout.strip().splitlines()[-1]
            orphan_server = libtmux.Server(socket_name=orphan_socket)
            assert orphan_server.sessions

            # Conversation B's agent-server starts on the same host.
            _cleanup_stale_tmux_sessions()

            # The live sibling is untouched and still usable.
            assert "A_STILL_ALIVE" in terminal_a.read_screen()
            terminal_a.send_keys("printf 'A_STILL_USABLE\\n'")
            _wait_for(lambda: "A_STILL_USABLE" in terminal_a.read_screen())

            # A socket whose owner is gone is reclaimed, the legacy socket is
            # still cleaned up, and non-OpenHands sockets are left alone.
            assert not orphan_server.sessions
            assert not legacy.sessions
            assert unrelated.sessions
        finally:
            pool_a.close()
            legacy.cmd("kill-server")
            unrelated.cmd("kill-server")


# Pause a real TmuxPanePool.initialize() inside the creation lock, after the
# tmux session (and therefore the socket) exists but before the owner option is
# recorded. A sibling cleanup running in the parent process must not reclaim
# the socket while it is held here.
_CONCURRENT_INIT_SCRIPT = """
import sys
from openhands.tools.terminal.terminal.tmux_pane_pool import TmuxPanePool

barrier_line = int(sys.argv[2])


def pause(frame, event, arg):
    if (
        event == "line"
        and frame.f_code.co_name == "_new_owned_session"
        and frame.f_code.co_filename.endswith("tmux_terminal.py")
        and frame.f_lineno == barrier_line
    ):
        print("CREATED", flush=True)
        sys.stdin.readline()
    return pause


sys.settrace(pause)
pool = TmuxPanePool(sys.argv[1])
try:
    pool.initialize()
    print("INITIALIZED", flush=True)
finally:
    pool.close()
"""


@pytest.mark.skipif(os.name != "posix", reason="tmux requires Unix")
def test_cleanup_stale_tmux_sessions_serializes_with_socket_creation(monkeypatch):
    """Startup cleanup must not reclaim a socket a live process is creating.

    Regression for the #5581 concurrent-startup gap: cleanup used to treat a
    socket as stale whenever it had no recorded owner. A sibling process creates
    its socket a moment before it can record ownership, so cleanup in that
    window killed a live pool and its terminal setup then failed. Cleanup and
    socket creation now share a cross-process lock, so cleanup cannot observe a
    half-created socket.
    """
    import inspect

    from openhands.tools.terminal.terminal import tmux_terminal

    # The owner is recorded by mark_socket_owner() *after* new_session() created
    # the socket; pause on that call to hit the window the lock must protect.
    source_lines, first_line = inspect.getsourcelines(tmux_terminal._new_owned_session)
    barrier_line = next(
        first_line + offset
        for offset, line in enumerate(source_lines)
        if "mark_socket_owner(" in line
    )

    with tempfile.TemporaryDirectory(
        prefix="oh-concurrent-init-", dir="/tmp"
    ) as socket_dir:
        monkeypatch.setenv("TMUX_TMPDIR", socket_dir)
        env = {**os.environ, "TMUX_TMPDIR": socket_dir}
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CONCURRENT_INIT_SCRIPT,
                socket_dir,
                str(barrier_line),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            # Blocks until the child has created its socket and paused, still
            # holding the creation lock, before recording its owner.
            assert child.stdout is not None
            created = child.stdout.readline().strip()
            assert created == "CREATED", created

            # Conversation B's agent-server starts on the same host. It must
            # wait for the creation lock rather than reclaim the live socket.
            cleanup_done = threading.Event()
            cleanup_error: list[BaseException] = []

            def run_cleanup() -> None:
                try:
                    _cleanup_stale_tmux_sessions()
                except BaseException as exc:  # pragma: no cover - reported below
                    cleanup_error.append(exc)
                finally:
                    cleanup_done.set()

            cleanup_thread = threading.Thread(target=run_cleanup)
            cleanup_thread.start()
            try:
                assert not cleanup_done.wait(timeout=3.0), (
                    "cleanup finished while socket creation held the lock; it "
                    "must not run concurrently with an in-progress creation"
                )

                # Let the child publish ownership and finish initializing.
                assert child.stdin is not None
                child.stdin.write("\n")
                child.stdin.flush()
                remaining_out, child_err = child.communicate(timeout=30)
            finally:
                cleanup_thread.join(timeout=30)

            assert not cleanup_error, cleanup_error
            assert cleanup_done.is_set()
            assert child.returncode == 0, child_err
            assert "INITIALIZED" in remaining_out
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


class TestStaticFilesServing:
    """Test static files serving functionality."""

    def test_static_files_not_mounted_when_path_none(self):
        """Test that static files are not mounted when static_files_path is None."""
        config = Config(static_files_path=None)
        app = create_app(config)
        client = TestClient(app)

        # Try to access static files endpoint - should return 404
        response = client.get("/static/test.txt")
        assert response.status_code == 404

    def test_static_files_not_mounted_when_directory_missing(self):
        """Test that static files are not mounted when directory doesn't exist."""
        config = Config(static_files_path=Path("/nonexistent/directory"))
        app = create_app(config)
        client = TestClient(app)

        # Try to access static files endpoint - should return 404
        response = client.get("/static/test.txt")
        assert response.status_code == 404

    def test_static_files_mounted_when_directory_exists(self):
        """Test that static files are mounted when directory exists."""
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir)

            # Create a test file
            test_file = static_dir / "test.txt"
            test_file.write_text("Hello, static world!")

            config = Config(static_files_path=static_dir)
            app = create_app(config)
            client = TestClient(app)

            # Access the static file
            response = client.get("/static/test.txt")
            assert response.status_code == 200
            assert response.text == "Hello, static world!"
            assert response.headers["content-type"] == "text/plain; charset=utf-8"

    def test_static_files_serve_html(self):
        """Test that static files can serve HTML files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir)

            # Create an HTML test file
            html_file = static_dir / "index.html"
            html_content = "<html><body><h1>Static HTML</h1></body></html>"
            html_file.write_text(html_content)

            config = Config(static_files_path=static_dir)
            app = create_app(config)
            client = TestClient(app)

            # Access the HTML file
            response = client.get("/static/index.html")
            assert response.status_code == 200
            assert response.text == html_content
            assert "text/html" in response.headers["content-type"]

    def test_static_files_serve_subdirectory(self):
        """Test that static files can serve files from subdirectories."""
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir)

            # Create a subdirectory with a file
            sub_dir = static_dir / "assets"
            sub_dir.mkdir()
            css_file = sub_dir / "style.css"
            css_content = "body { color: blue; }"
            css_file.write_text(css_content)

            config = Config(static_files_path=static_dir)
            app = create_app(config)
            client = TestClient(app)

            # Access the CSS file in subdirectory
            response = client.get("/static/assets/style.css")
            assert response.status_code == 200
            assert response.text == css_content
            assert "text/css" in response.headers["content-type"]

    def test_static_files_404_for_missing_file(self):
        """Test that missing static files return 404."""
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir)

            config = Config(static_files_path=static_dir)
            app = create_app(config)
            client = TestClient(app)

            # Try to access non-existent file
            response = client.get("/static/nonexistent.txt")
            assert response.status_code == 404

    def test_static_files_security_no_directory_traversal(self):
        """Test that directory traversal attacks are prevented."""
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir)

            # Create a file outside the static directory
            parent_dir = Path(temp_dir).parent
            secret_file = parent_dir / "secret.txt"
            secret_file.write_text("Secret content")

            config = Config(static_files_path=static_dir)
            app = create_app(config)
            client = TestClient(app)

            # Try directory traversal attack
            response = client.get("/static/../secret.txt")
            assert response.status_code == 404

        # Clean up the secret file
        if secret_file.exists():
            secret_file.unlink()


class TestRootRedirect:
    """Test root endpoint redirect functionality."""

    def test_root_redirect_to_index_html_when_exists(self):
        """Test that root endpoint redirects to /static/index.html when it exists."""
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir)

            # Create an index.html file
            index_file = static_dir / "index.html"
            index_file.write_text("<html><body><h1>Welcome</h1></body></html>")

            config = Config(static_files_path=static_dir)
            app = create_app(config)
            client = TestClient(app)

            # Test root redirect
            response = client.get("/", follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["location"] == "/static/index.html"

    def test_root_redirect_to_static_dir_when_no_index(self):
        """Test that root endpoint redirects to /static/ when no index.html exists."""
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir)

            # Create a different file (not index.html)
            other_file = static_dir / "other.html"
            other_file.write_text("<html><body><h1>Other</h1></body></html>")

            config = Config(static_files_path=static_dir)
            app = create_app(config)
            client = TestClient(app)

            # Test root redirect
            response = client.get("/", follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["location"] == "/static/"

    def test_root_redirect_follows_to_index_html(self):
        """Test that following the root redirect serves index.html when it exists."""
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir)

            # Create an index.html file
            index_file = static_dir / "index.html"
            index_content = "<html><body><h1>Welcome to Static Site</h1></body></html>"
            index_file.write_text(index_content)

            config = Config(static_files_path=static_dir)
            app = create_app(config)
            client = TestClient(app)

            # Test root redirect with follow_redirects=True
            response = client.get("/", follow_redirects=True)
            assert response.status_code == 200
            assert response.text == index_content
            assert "text/html" in response.headers["content-type"]

    def test_no_root_redirect_when_static_files_not_configured(self):
        """Test that root endpoint doesn't redirect when static files are not configured."""  # noqa: E501
        config = Config(static_files_path=None)
        app = create_app(config)
        client = TestClient(app)

        # Root should return 404 (no handler defined)
        response = client.get("/")
        assert response.status_code == 200

    def test_no_root_redirect_when_static_directory_missing(self):
        """Test that root endpoint doesn't redirect when static directory doesn't exist."""  # noqa: E501
        config = Config(static_files_path=Path("/nonexistent/directory"))
        app = create_app(config)
        client = TestClient(app)

        # Root should return 404 (no handler defined)
        response = client.get("/")
        assert response.status_code == 200


class TestServiceParallelization:
    """Test that services are started and stopped in parallel."""

    async def test_services_start_in_parallel(self):
        """Test that VSCode and Tool Preload services start concurrently."""
        # Create mock services that take some time to start
        mock_vscode_service = AsyncMock()
        mock_tool_preload_service = AsyncMock()
        mock_conversation_service = AsyncMock()

        active_starts = 0
        max_concurrent_starts = 0
        start_lock = asyncio.Lock()

        async def slow_start():
            nonlocal active_starts, max_concurrent_starts
            async with start_lock:
                active_starts += 1
                max_concurrent_starts = max(max_concurrent_starts, active_starts)

            await asyncio.sleep(0.1)

            async with start_lock:
                active_starts -= 1

            return True

        mock_vscode_service.start = AsyncMock(side_effect=slow_start)
        mock_tool_preload_service.start = AsyncMock(side_effect=slow_start)

        # Mock the service getters
        with (
            patch(
                "openhands.agent_server.api.ConversationService.get_instance",
                return_value=mock_conversation_service,
            ),
            patch(
                "openhands.agent_server.api.get_vscode_service",
                return_value=mock_vscode_service,
            ),
            patch(
                "openhands.agent_server.api.get_tool_preload_service",
                return_value=mock_tool_preload_service,
            ),
        ):
            # Create a mock FastAPI app
            mock_app = AsyncMock()
            mock_app.state = SimpleNamespace(config=Config())

            async with api_lifespan(mock_app):
                pass

            assert max_concurrent_starts == 2

            # Verify all services were started
            mock_vscode_service.start.assert_called_once()
            mock_tool_preload_service.start.assert_called_once()

    async def test_services_stop_in_parallel(self):
        """Test that VSCode and Tool Preload services stop concurrently."""
        # Create mock services that take some time to stop
        mock_vscode_service = AsyncMock()
        mock_tool_preload_service = AsyncMock()
        mock_conversation_service = AsyncMock()

        # Make each service take 0.1 seconds to stop
        async def slow_stop():
            await asyncio.sleep(0.1)

        mock_vscode_service.start = AsyncMock(return_value=True)
        mock_tool_preload_service.start = AsyncMock(return_value=True)
        mock_vscode_service.stop = AsyncMock(side_effect=slow_stop)
        mock_tool_preload_service.stop = AsyncMock(side_effect=slow_stop)

        # Mock the service getters
        with (
            patch(
                "openhands.agent_server.api.ConversationService.get_instance",
                return_value=mock_conversation_service,
            ),
            patch(
                "openhands.agent_server.api.get_vscode_service",
                return_value=mock_vscode_service,
            ),
            patch(
                "openhands.agent_server.api.get_tool_preload_service",
                return_value=mock_tool_preload_service,
            ),
        ):
            # Create a mock FastAPI app
            mock_app = AsyncMock()
            mock_backend_manager = AsyncMock()
            mock_app.state = SimpleNamespace(
                config=Config(),
                canvas_extension_backend_manager=mock_backend_manager,
            )

            async with api_lifespan(mock_app):
                # Exit the context to trigger shutdown
                pass

            # Verify all services were stopped
            mock_vscode_service.stop.assert_called_once()
            mock_tool_preload_service.stop.assert_called_once()
            mock_backend_manager.shutdown.assert_awaited_once()

    async def test_services_handle_none_values(self):
        """Test that the lifespan handles None service values correctly."""
        mock_conversation_service = AsyncMock()

        # Mock all services as None (disabled)
        with (
            patch(
                "openhands.agent_server.api.ConversationService.get_instance",
                return_value=mock_conversation_service,
            ),
            patch("openhands.agent_server.api.get_vscode_service", return_value=None),
            patch(
                "openhands.agent_server.api.get_tool_preload_service", return_value=None
            ),
        ):
            # Create a mock FastAPI app
            mock_app = AsyncMock()
            mock_app.state = SimpleNamespace(config=Config())

            # This should not raise any exceptions
            async with api_lifespan(mock_app):
                pass

            # Verify conversation service was set up
            assert mock_app.state.conversation_service == mock_conversation_service

    async def test_registry_starts_before_conversation_recovery(self):
        events = []
        registry = SimpleNamespace(
            configure_service=lambda _service: events.append("configure"),
            start=AsyncMock(side_effect=lambda: events.append("registry")),
            shutdown=AsyncMock(),
        )
        service = AsyncMock()
        service.__aenter__.side_effect = lambda: events.append("service") or service

        with (
            patch(
                "openhands.agent_server.api.ConversationService.get_instance",
                return_value=service,
            ),
            patch("openhands.agent_server.api.get_vscode_service", return_value=None),
            patch(
                "openhands.agent_server.api.get_tool_preload_service",
                return_value=None,
            ),
        ):
            mock_app = AsyncMock()
            mock_app.state = SimpleNamespace(
                config=Config(), conversation_registry=registry
            )

            async with api_lifespan(mock_app):
                pass

        assert events[:3] == ["configure", "registry", "service"]

    async def test_lifespan_defaults_and_restores_tmux_tmpdir(
        self, short_tmp_path, monkeypatch
    ):
        """Test that lifespan defaults TMUX_TMPDIR per server instance."""
        expected_tmux_tmpdir = short_tmp_path / "tmux"
        monkeypatch.setattr(
            "openhands.agent_server.api._default_server_tmux_tmpdir",
            lambda: expected_tmux_tmpdir,
        )
        mock_conversation_service = AsyncMock()

        with (
            patch(
                "openhands.agent_server.api.ConversationService.get_instance",
                return_value=mock_conversation_service,
            ),
            patch("openhands.agent_server.api.get_vscode_service", return_value=None),
            patch(
                "openhands.agent_server.api.get_tool_preload_service", return_value=None
            ),
        ):
            mock_app = AsyncMock()
            mock_app.state = SimpleNamespace(config=Config())
            async with api_lifespan(mock_app):
                assert os.environ["TMUX_TMPDIR"] == str(expected_tmux_tmpdir)
                assert expected_tmux_tmpdir.is_dir()

            assert "TMUX_TMPDIR" not in os.environ
            assert not expected_tmux_tmpdir.exists()

    async def test_lifespan_keeps_user_provided_tmux_tmpdir(
        self, short_tmp_path, monkeypatch
    ):
        """A TMUX_TMPDIR the user set is never removed at shutdown."""
        user_tmux_tmpdir = short_tmp_path / "user-tmux"
        user_tmux_tmpdir.mkdir()
        monkeypatch.setenv("TMUX_TMPDIR", str(user_tmux_tmpdir))
        mock_conversation_service = AsyncMock()

        with (
            patch(
                "openhands.agent_server.api.ConversationService.get_instance",
                return_value=mock_conversation_service,
            ),
            patch("openhands.agent_server.api.get_vscode_service", return_value=None),
            patch(
                "openhands.agent_server.api.get_tool_preload_service", return_value=None
            ),
        ):
            mock_app = AsyncMock()
            mock_app.state = SimpleNamespace(config=Config())
            async with api_lifespan(mock_app):
                assert os.environ["TMUX_TMPDIR"] == str(user_tmux_tmpdir)

            assert os.environ["TMUX_TMPDIR"] == str(user_tmux_tmpdir)
            assert user_tmux_tmpdir.is_dir()

    async def test_lifespan_removes_substitute_for_too_deep_tmux_tmpdir(
        self, deep_tmp_path, short_tmp_path, monkeypatch
    ):
        """A too-long TMUX_TMPDIR is restored and kept; the substitute removed."""
        monkeypatch.setenv("TMUX_TMPDIR", str(deep_tmp_path))
        substitute = short_tmp_path / "tmux"
        monkeypatch.setattr(
            "openhands.agent_server.api._default_server_tmux_tmpdir",
            lambda: substitute,
        )
        monkeypatch.setattr(
            "openhands.agent_server.api._fits_tmux_socket",
            lambda path: path == substitute,
        )
        mock_conversation_service = AsyncMock()

        with (
            patch(
                "openhands.agent_server.api.ConversationService.get_instance",
                return_value=mock_conversation_service,
            ),
            patch("openhands.agent_server.api.get_vscode_service", return_value=None),
            patch(
                "openhands.agent_server.api.get_tool_preload_service", return_value=None
            ),
        ):
            mock_app = AsyncMock()
            mock_app.state = SimpleNamespace(config=Config())
            async with api_lifespan(mock_app):
                assert os.environ["TMUX_TMPDIR"] == str(substitute)

            assert os.environ["TMUX_TMPDIR"] == str(deep_tmp_path)
            assert deep_tmp_path.is_dir()
            assert not substitute.exists()

    async def test_lifespan_logs_and_continues_when_tmux_tmpdir_removal_fails(
        self, short_tmp_path, monkeypatch, caplog
    ):
        """A failed removal is logged and does not abort shutdown."""
        expected_tmux_tmpdir = short_tmp_path / "tmux"
        monkeypatch.setattr(
            "openhands.agent_server.api._default_server_tmux_tmpdir",
            lambda: expected_tmux_tmpdir,
        )

        def fail_rmtree(path):
            raise PermissionError("denied")

        # Replace the module's shutil, not shutil.rmtree itself, so fixture
        # teardown can still remove the directory.
        monkeypatch.setattr(
            "openhands.agent_server.api.shutil", SimpleNamespace(rmtree=fail_rmtree)
        )
        mock_conversation_service = AsyncMock()

        with (
            patch(
                "openhands.agent_server.api.ConversationService.get_instance",
                return_value=mock_conversation_service,
            ),
            patch("openhands.agent_server.api.get_vscode_service", return_value=None),
            patch(
                "openhands.agent_server.api.get_tool_preload_service", return_value=None
            ),
        ):
            mock_app = AsyncMock()
            mock_app.state = SimpleNamespace(config=Config())
            with caplog.at_level("WARNING"):
                async with api_lifespan(mock_app):
                    pass

            assert "TMUX_TMPDIR" not in os.environ
            assert "Failed to remove per-server tmux directory" in caplog.text

    async def test_lifespan_keeps_preexisting_default_tmux_tmpdir(
        self, short_tmp_path, monkeypatch
    ):
        """A default directory that existed before startup is not removed."""
        expected_tmux_tmpdir = short_tmp_path / "tmux"
        expected_tmux_tmpdir.mkdir()
        monkeypatch.setattr(
            "openhands.agent_server.api._default_server_tmux_tmpdir",
            lambda: expected_tmux_tmpdir,
        )
        mock_conversation_service = AsyncMock()

        with (
            patch(
                "openhands.agent_server.api.ConversationService.get_instance",
                return_value=mock_conversation_service,
            ),
            patch("openhands.agent_server.api.get_vscode_service", return_value=None),
            patch(
                "openhands.agent_server.api.get_tool_preload_service", return_value=None
            ),
        ):
            mock_app = AsyncMock()
            mock_app.state = SimpleNamespace(config=Config())
            async with api_lifespan(mock_app):
                assert os.environ["TMUX_TMPDIR"] == str(expected_tmux_tmpdir)

            assert "TMUX_TMPDIR" not in os.environ
            assert expected_tmux_tmpdir.is_dir()


class TestRootPath:
    """Tests for _get_root_path function and root_path configuration."""

    def test_get_root_path_returns_slash_when_web_url_is_none(self):
        """Test that _get_root_path returns '' when web_url is not configured."""
        config = Config(web_url=None)
        assert _get_root_path(config) == ""

    def test_get_root_path_extracts_path_from_url(self):
        """Test that _get_root_path extracts the path component from web_url."""
        config = Config(web_url="https://example.com/runtime/123")
        assert _get_root_path(config) == "/runtime/123"

    def test_get_root_path_returns_slash_for_root_url(self):
        """Test that _get_root_path returns '/' for a URL without path."""
        config = Config(web_url="https://example.com")
        assert _get_root_path(config) == ""

    def test_get_root_path_with_trailing_slash(self):
        """Test that _get_root_path preserves trailing slash."""
        config = Config(web_url="https://example.com/api/")
        assert _get_root_path(config) == "/api"

    def test_get_root_path_with_complex_path(self):
        """Test _get_root_path with a complex nested path."""
        config = Config(
            web_url="https://work-1-abc123.prod-runtime.all-hands.dev/runtime/456/api"
        )
        assert _get_root_path(config) == "/runtime/456/api"

    def test_fastapi_instance_uses_root_path(self):
        """Test that FastAPI instance is created with correct root_path."""
        config = Config(web_url="https://example.com/mypath")
        app = create_app(config)
        assert app.root_path == "/mypath"

    def test_fastapi_instance_uses_default_root_path_when_no_web_url(self):
        """Test that FastAPI instance uses '/' root_path when web_url is None."""
        config = Config(web_url=None)
        app = create_app(config)
        assert app.root_path == ""


class TestConfigWebUrl:
    """Tests for web_url configuration field."""

    def test_web_url_default_is_none_when_env_not_set(self):
        """Test that web_url defaults to None when no env vars are set."""
        with patch.dict("os.environ", {}, clear=True):
            config = Config()
            assert config.web_url is None

    def test_web_url_reads_from_oh_web_url_env(self):
        """Test that web_url reads from the canonical OH_WEB_URL env var."""
        with patch.dict("os.environ", {"OH_WEB_URL": "https://test.example.com/path"}):
            config = Config()
            assert config.web_url == "https://test.example.com/path"

    def test_web_url_ignores_legacy_runtime_url_env(self):
        """Test that deprecated RUNTIME_URL no longer configures web_url."""
        with patch.dict("os.environ", {"RUNTIME_URL": "https://test.example.com/path"}):
            config = Config()

        assert config.web_url is None

    def test_web_url_reads_oh_web_url_when_runtime_url_is_also_set(self):
        """Test that OH_WEB_URL remains authoritative."""
        with patch.dict(
            "os.environ",
            {
                "OH_WEB_URL": "https://preferred.example.com/path",
                "RUNTIME_URL": "https://legacy.example.com/path",
            },
        ):
            config = Config()

        assert config.web_url == "https://preferred.example.com/path"

    def test_web_url_can_be_set_explicitly(self):
        """Test that web_url can be set explicitly, overriding env vars."""
        with patch.dict(
            "os.environ",
            {
                "OH_WEB_URL": "https://env.example.com/oh",
                "RUNTIME_URL": "https://env.example.com/runtime",
            },
        ):
            config = Config(web_url="https://explicit.example.com/custom")
            assert config.web_url == "https://explicit.example.com/custom"


@pytest.mark.parametrize(
    "web_url,expected_root_path",
    [
        (None, ""),
        ("https://example.com", ""),
        ("https://example.com/", ""),
        ("https://example.com/api", "/api"),
        ("https://example.com/api/v1", "/api/v1"),
        ("http://localhost:8000/test", "/test"),
        ("https://work-1-xyz.prod-runtime.all-hands.dev/runtime/abc", "/runtime/abc"),
    ],
)
def test_get_root_path_parametrized(web_url, expected_root_path):
    """Parametrized test for _get_root_path with various URL patterns."""
    config = Config(web_url=web_url)
    assert _get_root_path(config) == expected_root_path


class TestHttpExceptionLogging:
    """5xx HTTPExceptions are intentionally-raised flow control.

    They should be logged as a single ERROR line without a full stack
    trace; only genuinely unhandled exceptions should get a traceback.
    Otherwise routine upstream blips look indistinguishable from a process
    crash in the logs.
    """

    def _build_app_with_failing_route(self, status_code: int):
        from fastapi import HTTPException as FastAPIHTTPException

        config = Config(static_files_path=None)
        app = create_app(config)

        @app.get(f"/__test__/raise_{status_code}")
        def _raise():
            raise FastAPIHTTPException(
                status_code=status_code, detail="boom from upstream"
            )

        return app

    def test_5xx_http_exception_logged_without_traceback_by_default(self, caplog):
        import logging

        app = self._build_app_with_failing_route(502)
        client = TestClient(app)

        with caplog.at_level(logging.ERROR, logger="openhands.agent_server.api"):
            response = client.get("/__test__/raise_502")

        assert response.status_code == 502
        # Client still sees the same sanitized 5xx envelope.
        assert response.json()["detail"] == "Internal Server Error"

        api_error_records = [
            r
            for r in caplog.records
            if r.name == "openhands.agent_server.api" and r.levelno == logging.ERROR
        ]
        assert len(api_error_records) == 1, (
            "Expected exactly one ERROR log line for a 5xx HTTPException, "
            f"got: {[r.getMessage() for r in api_error_records]}"
        )
        record = api_error_records[0]
        # The whole point of the fix: no stack trace attached for an
        # intentionally-raised HTTPException.
        assert record.exc_info is None, (
            "5xx HTTPException should not log a traceback by default; "
            f"got exc_info={record.exc_info!r}"
        )
        # Message must still carry status, method, path, and detail so
        # the log is useful for monitoring.
        message = record.getMessage()
        assert "502" in message
        assert "GET" in message
        assert "/__test__/raise_502" in message
        assert "boom from upstream" in message

    def test_5xx_http_exception_includes_traceback_when_debug_enabled(
        self, caplog, monkeypatch
    ):
        import logging

        # DEBUG is read at module import time in api.py, so monkeypatch
        # the bound name on the module rather than mutating the env.
        monkeypatch.setattr("openhands.agent_server.api.DEBUG", True)

        app = self._build_app_with_failing_route(503)
        client = TestClient(app)

        with caplog.at_level(logging.ERROR, logger="openhands.agent_server.api"):
            response = client.get("/__test__/raise_503")

        assert response.status_code == 503
        api_error_records = [
            r
            for r in caplog.records
            if r.name == "openhands.agent_server.api" and r.levelno == logging.ERROR
        ]
        assert len(api_error_records) == 1
        # In DEBUG mode the traceback is preserved as an opt-in debugging aid.
        assert api_error_records[0].exc_info is not None

    def test_4xx_http_exception_logged_at_info_without_traceback(self, caplog):
        import logging

        app = self._build_app_with_failing_route(404)
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="openhands.agent_server.api"):
            response = client.get("/__test__/raise_404")

        assert response.status_code == 404
        # 4xx path returns the raw detail (not the sanitized 5xx envelope).
        assert response.json() == {"detail": "boom from upstream"}

        api_records = [
            r for r in caplog.records if r.name == "openhands.agent_server.api"
        ]
        # No ERROR-level noise for a routine 4xx.
        assert not any(r.levelno >= logging.ERROR for r in api_records)
        info_records = [r for r in api_records if r.levelno == logging.INFO]
        assert info_records, "Expected an INFO log line for a 4xx HTTPException"
        assert all(r.exc_info is None for r in info_records)


def test_lifespan_conversation_service_uses_app_config_not_env(tmp_path, monkeypatch):
    """create_app(config) must persist and notify per that config, not OH_* env.

    Inside an OpenHands sandbox these env vars point at the live conversations
    directory and the app server's webhook endpoint, so a test app that fell
    back to them would write into, and report to, the real deployment.
    """
    monkeypatch.setenv("OH_CONVERSATIONS_PATH", str(tmp_path / "env-conversations"))
    monkeypatch.setenv("OH_WEBHOOKS_0_BASE_URL", "http://env-webhook.invalid/hooks")
    monkeypatch.setattr(config_module, "_default_config", None)
    monkeypatch.setattr(conversation_service_module, "_conversation_service", None)
    config = Config(
        static_files_path=None,
        session_api_keys=[],
        conversations_path=tmp_path / "conversations",
        bash_events_dir=tmp_path / "bash_events",
    )

    app = create_app(config)
    with TestClient(app):
        service = app.state.conversation_service
        assert service.conversations_dir == tmp_path / "conversations"
        assert service.webhook_specs == []
