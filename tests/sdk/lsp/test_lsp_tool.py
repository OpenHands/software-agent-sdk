"""Tests for LSP tool functionality."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openhands.sdk.lsp.definition import (
    LSPOperation,
    LSPToolAction,
    LSPToolObservation,
    _extract_hover_content,
    _uri_to_path,
)
from openhands.sdk.lsp.exceptions import (
    LSPConnectionError,
    LSPError,
    LSPServerError,
    LSPServerNotFoundError,
    LSPTimeoutError,
)
from openhands.sdk.lsp.manager import LSPServerConfig, LSPServerManager
from openhands.sdk.lsp.tool import LSPToolDefinition, LSPToolExecutor
from openhands.sdk.lsp.utils import create_lsp_tools, validate_lsp_config


class TestLSPToolAction:
    """Tests for LSPToolAction schema."""

    def test_create_definition_action(self):
        """Test creating a definition action."""
        action = LSPToolAction(
            operation=LSPOperation.DEFINITION,
            file_path="/path/to/file.ts",
            line=10,
            character=5,
        )
        assert action.operation == LSPOperation.DEFINITION
        assert action.file_path == "/path/to/file.ts"
        assert action.line == 10
        assert action.character == 5
        assert action.include_declaration is True  # default

    def test_create_references_action(self):
        """Test creating a references action with include_declaration."""
        action = LSPToolAction(
            operation=LSPOperation.REFERENCES,
            file_path="/path/to/file.py",
            line=20,
            character=10,
            include_declaration=False,
        )
        assert action.operation == LSPOperation.REFERENCES
        assert action.include_declaration is False

    def test_create_hover_action(self):
        """Test creating a hover action."""
        action = LSPToolAction(
            operation=LSPOperation.HOVER,
            file_path="/path/to/file.go",
            line=1,
            character=0,
        )
        assert action.operation == LSPOperation.HOVER


class TestLSPToolObservation:
    """Tests for LSPToolObservation schema."""

    def test_from_locations_with_results(self):
        """Test creating observation from location results."""
        locations = [
            {
                "uri": "file:///path/to/definition.ts",
                "range": {
                    "start": {"line": 10, "character": 0},
                    "end": {"line": 10, "character": 20},
                },
            },
            {
                "uri": "file:///path/to/other.ts",
                "range": {
                    "start": {"line": 5, "character": 4},
                    "end": {"line": 5, "character": 15},
                },
            },
        ]
        obs = LSPToolObservation.from_locations(
            LSPOperation.DEFINITION, "/query/file.ts", locations
        )
        assert not obs.is_error
        assert "Found 2 definition(s)" in obs.text
        assert "/path/to/definition.ts:11:0" in obs.text  # 1-indexed
        assert "/path/to/other.ts:6:4" in obs.text

    def test_from_locations_empty(self):
        """Test creating observation from empty locations."""
        obs = LSPToolObservation.from_locations(
            LSPOperation.REFERENCES, "/query/file.ts", []
        )
        assert not obs.is_error
        assert "No references found" in obs.text

    def test_from_hover_with_result(self):
        """Test creating observation from hover result."""
        hover = {
            "contents": {"kind": "markdown", "value": "**function** `greet(name: string)`"}
        }
        obs = LSPToolObservation.from_hover("/query/file.ts", hover)
        assert not obs.is_error
        assert "**function**" in obs.text

    def test_from_hover_none(self):
        """Test creating observation from None hover result."""
        obs = LSPToolObservation.from_hover("/query/file.ts", None)
        assert not obs.is_error
        assert "No hover information available" in obs.text

    def test_from_error(self):
        """Test creating error observation."""
        obs = LSPToolObservation.from_error(
            LSPOperation.DEFINITION, "/query/file.ts", "Connection failed"
        )
        assert obs.is_error
        assert "Connection failed" in obs.text


class TestHoverContentExtraction:
    """Tests for hover content extraction helper."""

    def test_extract_string_content(self):
        """Test extracting plain string content."""
        assert _extract_hover_content("Hello world") == "Hello world"

    def test_extract_markup_content(self):
        """Test extracting MarkupContent."""
        content = {"kind": "markdown", "value": "# Header\nText"}
        assert _extract_hover_content(content) == "# Header\nText"

    def test_extract_marked_string_with_language(self):
        """Test extracting MarkedString with language."""
        content = {"language": "typescript", "value": "const x: number = 5"}
        result = _extract_hover_content(content)
        assert "```typescript" in result
        assert "const x: number = 5" in result

    def test_extract_list_content(self):
        """Test extracting list of content items."""
        content = [
            {"kind": "markdown", "value": "First"},
            "Second",
            {"language": "ts", "value": "code"},
        ]
        result = _extract_hover_content(content)
        assert "First" in result
        assert "Second" in result
        assert "code" in result


class TestUriToPath:
    """Tests for URI to path conversion."""

    def test_file_uri_unix(self):
        """Test converting file:// URI on Unix."""
        uri = "file:///home/user/project/file.ts"
        assert _uri_to_path(uri) == "/home/user/project/file.ts"

    def test_file_uri_with_spaces(self):
        """Test converting URI with encoded spaces."""
        uri = "file:///path/with%20spaces/file.ts"
        assert _uri_to_path(uri) == "/path/with spaces/file.ts"

    def test_non_file_uri(self):
        """Test passing non-file URI returns as-is."""
        uri = "https://example.com/file.ts"
        assert _uri_to_path(uri) == uri


class TestLSPExceptions:
    """Tests for LSP exception classes."""

    def test_lsp_error_base(self):
        """Test base LSP error."""
        error = LSPError("Something went wrong")
        assert str(error) == "Something went wrong"

    def test_lsp_timeout_error(self):
        """Test timeout error with details."""
        error = LSPTimeoutError(
            "Request timed out",
            timeout=30.0,
            server_name="typescript",
            operation="textDocument/definition",
        )
        assert error.timeout == 30.0
        assert error.server_name == "typescript"
        assert error.operation == "textDocument/definition"

    def test_lsp_server_error(self):
        """Test server error with code and data."""
        error = LSPServerError(
            "Method not found",
            code=-32601,
            data={"method": "unknown"},
        )
        assert error.code == -32601
        assert error.data == {"method": "unknown"}

    def test_lsp_server_not_found_error(self):
        """Test server not found error."""
        error = LSPServerNotFoundError("/path/to/file.xyz", ".xyz")
        assert error.file_path == "/path/to/file.xyz"
        assert error.extension == ".xyz"
        assert ".xyz" in str(error)

    def test_lsp_connection_error(self):
        """Test connection error."""
        error = LSPConnectionError("Failed to connect", server_name="pyright")
        assert error.server_name == "pyright"


class TestLSPServerConfig:
    """Tests for LSP server configuration."""

    def test_from_dict_basic(self):
        """Test creating config from basic dict."""
        data = {
            "command": "typescript-language-server",
            "args": ["--stdio"],
        }
        config = LSPServerConfig.from_dict(data)
        assert config.command == "typescript-language-server"
        assert config.args == ["--stdio"]
        assert config.extension_to_language == {}

    def test_from_dict_with_extensions(self):
        """Test creating config with extension mappings."""
        data = {
            "command": "pyright",
            "args": ["--stdio"],
            "extensionToLanguage": {
                ".py": "python",
                ".pyi": "python",
            },
        }
        config = LSPServerConfig.from_dict(data)
        assert config.extension_to_language == {".py": "python", ".pyi": "python"}

    def test_from_dict_snake_case(self):
        """Test creating config with snake_case keys."""
        data = {
            "command": "gopls",
            "extension_to_language": {".go": "go"},
        }
        config = LSPServerConfig.from_dict(data)
        assert config.extension_to_language == {".go": "go"}


class TestLSPServerManager:
    """Tests for LSP server manager."""

    def test_init_with_config(self):
        """Test initializing manager with config."""
        config = {
            "lspServers": {
                "typescript": {
                    "command": "typescript-language-server",
                    "args": ["--stdio"],
                    "extensionToLanguage": {".ts": "typescript"},
                }
            }
        }
        manager = LSPServerManager(config, "/workspace")
        assert "typescript" in manager._server_configs

    def test_get_server_for_file(self):
        """Test file to server routing."""
        config = {
            "servers": {
                "typescript": {
                    "command": "tsserver",
                    "extensionToLanguage": {".ts": "typescript", ".tsx": "typescriptreact"},
                },
                "python": {
                    "command": "pyright",
                    "extensionToLanguage": {".py": "python"},
                },
            }
        }
        manager = LSPServerManager(config, "/workspace")

        assert manager.get_server_for_file("/path/file.ts") == "typescript"
        assert manager.get_server_for_file("/path/file.tsx") == "typescript"
        assert manager.get_server_for_file("/path/file.py") == "python"
        assert manager.get_server_for_file("/path/file.xyz") is None

    def test_get_language_id(self):
        """Test getting language ID for file."""
        config = {
            "servers": {
                "typescript": {
                    "command": "tsserver",
                    "extensionToLanguage": {".ts": "typescript", ".tsx": "typescriptreact"},
                }
            }
        }
        manager = LSPServerManager(config, "/workspace")

        assert manager.get_language_id("/path/file.ts") == "typescript"
        assert manager.get_language_id("/path/file.tsx") == "typescriptreact"
        assert manager.get_language_id("/path/file.xyz") is None


class TestValidateLspConfig:
    """Tests for LSP config validation."""

    def test_valid_config(self):
        """Test validating correct config."""
        config = {
            "lspServers": {
                "typescript": {
                    "command": "typescript-language-server",
                    "args": ["--stdio"],
                    "extensionToLanguage": {".ts": "typescript"},
                }
            }
        }
        errors = validate_lsp_config(config)
        assert errors == []

    def test_missing_command(self):
        """Test validation catches missing command."""
        config = {
            "lspServers": {
                "typescript": {
                    "args": ["--stdio"],
                    "extensionToLanguage": {".ts": "typescript"},
                }
            }
        }
        errors = validate_lsp_config(config)
        assert any("command" in e for e in errors)

    def test_missing_extension_mapping(self):
        """Test validation warns about missing extension mapping."""
        config = {
            "lspServers": {
                "typescript": {
                    "command": "tsserver",
                }
            }
        }
        errors = validate_lsp_config(config)
        assert any("extensionToLanguage" in e for e in errors)

    def test_invalid_servers_type(self):
        """Test validation catches invalid servers type."""
        config = {"lspServers": "invalid"}
        errors = validate_lsp_config(config)
        assert any("dictionary" in e for e in errors)


class TestCreateLspTools:
    """Tests for create_lsp_tools utility."""

    def test_empty_config_returns_empty_list(self):
        """Test that empty config returns empty list."""
        tools = create_lsp_tools({}, "/workspace")
        assert tools == []

    def test_no_servers_returns_empty_list(self):
        """Test that config with no servers returns empty list."""
        tools = create_lsp_tools({"lspServers": {}}, "/workspace")
        assert tools == []

    def test_creates_tool_with_valid_config(self):
        """Test creating tool with valid config."""
        config = {
            "lspServers": {
                "typescript": {
                    "command": "typescript-language-server",
                    "args": ["--stdio"],
                    "extensionToLanguage": {".ts": "typescript"},
                }
            }
        }
        tools = create_lsp_tools(config, "/workspace")
        assert len(tools) == 1
        assert tools[0].name == "lsp"


class TestLSPToolDefinition:
    """Tests for LSPToolDefinition class."""

    def test_create_tool(self):
        """Test creating LSP tool definition."""
        config = {
            "servers": {
                "typescript": {
                    "command": "tsserver",
                    "extensionToLanguage": {".ts": "typescript"},
                }
            }
        }
        tools = LSPToolDefinition.create(config, "/workspace")
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "lsp"
        assert tool.action_type == LSPToolAction
        assert tool.observation_type == LSPToolObservation
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True


class TestLSPToolExecutor:
    """Tests for LSPToolExecutor."""

    def test_file_not_found(self, tmp_path: Path):
        """Test executor returns error for non-existent file."""
        config = {
            "servers": {
                "typescript": {
                    "command": "tsserver",
                    "extensionToLanguage": {".ts": "typescript"},
                }
            }
        }
        manager = LSPServerManager(config, str(tmp_path))
        executor = LSPToolExecutor(manager)

        action = LSPToolAction(
            operation=LSPOperation.DEFINITION,
            file_path="/nonexistent/file.ts",
            line=1,
            character=0,
        )
        result = executor(action)
        assert result.is_error
        assert "File not found" in result.text

    def test_no_server_for_extension(self, tmp_path: Path):
        """Test executor returns error when no server handles file type."""
        # Create a file with unsupported extension
        test_file = tmp_path / "file.xyz"
        test_file.write_text("content")

        config = {
            "servers": {
                "typescript": {
                    "command": "tsserver",
                    "extensionToLanguage": {".ts": "typescript"},
                }
            }
        }
        manager = LSPServerManager(config, str(tmp_path))
        executor = LSPToolExecutor(manager)

        action = LSPToolAction(
            operation=LSPOperation.DEFINITION,
            file_path=str(test_file),
            line=1,
            character=0,
        )
        result = executor(action)
        assert result.is_error
        assert "No LSP server configured" in result.text
