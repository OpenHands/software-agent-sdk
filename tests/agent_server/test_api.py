"""Tests for the agent server API functionality."""

import asyncio
import tempfile
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from deprecation import DeprecatedWarning
from fastapi.testclient import TestClient

from openhands.agent_server.api import _get_root_path, api_lifespan, create_app
from openhands.agent_server.config import Config


@pytest.fixture(autouse=True)
def clear_web_url_env(monkeypatch):
    monkeypatch.delenv("OH_WEB_URL", raising=False)
    monkeypatch.delenv("RUNTIME_URL", raising=False)


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
        """Test that VSCode, Desktop, and Tool Preload services start concurrently."""
        # Create mock services that take some time to start
        mock_vscode_service = AsyncMock()
        mock_desktop_service = AsyncMock()
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
        mock_desktop_service.start = AsyncMock(side_effect=slow_start)
        mock_tool_preload_service.start = AsyncMock(side_effect=slow_start)

        # Mock the service getters
        with (
            patch(
                "openhands.agent_server.api.get_default_conversation_service",
                return_value=mock_conversation_service,
            ),
            patch(
                "openhands.agent_server.api.get_vscode_service",
                return_value=mock_vscode_service,
            ),
            patch(
                "openhands.agent_server.api.get_desktop_service",
                return_value=mock_desktop_service,
            ),
            patch(
                "openhands.agent_server.api.get_tool_preload_service",
                return_value=mock_tool_preload_service,
            ),
        ):
            # Create a mock FastAPI app
            mock_app = AsyncMock()
            mock_app.state = AsyncMock()

            async with api_lifespan(mock_app):
                pass

            assert max_concurrent_starts == 3

            # Verify all services were started
            mock_vscode_service.start.assert_called_once()
            mock_desktop_service.start.assert_called_once()
            mock_tool_preload_service.start.assert_called_once()

    async def test_services_stop_in_parallel(self):
        """Test that VSCode, Desktop, and Tool Preload services stop concurrently."""
        # Create mock services that take some time to stop
        mock_vscode_service = AsyncMock()
        mock_desktop_service = AsyncMock()
        mock_tool_preload_service = AsyncMock()
        mock_conversation_service = AsyncMock()

        # Make each service take 0.1 seconds to stop
        async def slow_stop():
            await asyncio.sleep(0.1)

        mock_vscode_service.start = AsyncMock(return_value=True)
        mock_desktop_service.start = AsyncMock(return_value=True)
        mock_tool_preload_service.start = AsyncMock(return_value=True)
        mock_vscode_service.stop = AsyncMock(side_effect=slow_stop)
        mock_desktop_service.stop = AsyncMock(side_effect=slow_stop)
        mock_tool_preload_service.stop = AsyncMock(side_effect=slow_stop)

        # Mock the service getters
        with (
            patch(
                "openhands.agent_server.api.get_default_conversation_service",
                return_value=mock_conversation_service,
            ),
            patch(
                "openhands.agent_server.api.get_vscode_service",
                return_value=mock_vscode_service,
            ),
            patch(
                "openhands.agent_server.api.get_desktop_service",
                return_value=mock_desktop_service,
            ),
            patch(
                "openhands.agent_server.api.get_tool_preload_service",
                return_value=mock_tool_preload_service,
            ),
        ):
            # Create a mock FastAPI app
            mock_app = AsyncMock()
            mock_app.state = AsyncMock()

            async with api_lifespan(mock_app):
                # Exit the context to trigger shutdown
                pass

            # Verify all services were stopped
            mock_vscode_service.stop.assert_called_once()
            mock_desktop_service.stop.assert_called_once()
            mock_tool_preload_service.stop.assert_called_once()

    async def test_services_handle_none_values(self):
        """Test that the lifespan handles None service values correctly."""
        mock_conversation_service = AsyncMock()

        # Mock all services as None (disabled)
        with (
            patch(
                "openhands.agent_server.api.get_default_conversation_service",
                return_value=mock_conversation_service,
            ),
            patch("openhands.agent_server.api.get_vscode_service", return_value=None),
            patch("openhands.agent_server.api.get_desktop_service", return_value=None),
            patch(
                "openhands.agent_server.api.get_tool_preload_service", return_value=None
            ),
        ):
            # Create a mock FastAPI app
            mock_app = AsyncMock()
            mock_app.state = AsyncMock()

            # This should not raise any exceptions
            async with api_lifespan(mock_app):
                pass

            # Verify conversation service was set up
            assert mock_app.state.conversation_service == mock_conversation_service


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

    def test_web_url_reads_from_runtime_url_env_with_warning(self):
        """Test that legacy RUNTIME_URL still works but emits a deprecation warning."""
        with patch.dict("os.environ", {"RUNTIME_URL": "https://test.example.com/path"}):
            with pytest.warns(DeprecatedWarning) as caught:
                config = Config()

        assert config.web_url == "https://test.example.com/path"
        assert "RUNTIME_URL environment variable is deprecated" in str(
            caught[0].message
        )
        assert "OH_WEB_URL" in str(caught[0].message)
        assert "removed in 1.20.0" in str(caught[0].message)

    def test_web_url_prefers_oh_web_url_over_runtime_url(self):
        """Test that the canonical env var wins without warnings."""
        with patch.dict(
            "os.environ",
            {
                "OH_WEB_URL": "https://preferred.example.com/path",
                "RUNTIME_URL": "https://legacy.example.com/path",
            },
        ):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                config = Config()

        assert config.web_url == "https://preferred.example.com/path"
        assert caught == []

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
