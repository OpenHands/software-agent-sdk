"""LSP Server Manager for managing multiple LSP servers."""

import threading
from pathlib import Path
from typing import Any

from openhands.sdk.logger import get_logger
from openhands.sdk.lsp.client import LSPClient


logger = get_logger(__name__)


class LSPServerConfig:
    """Configuration for a single LSP server."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        extension_to_language: dict[str, str] | None = None,
        initialization_options: dict[str, Any] | None = None,
    ):
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.extension_to_language = extension_to_language or {}
        self.initialization_options = initialization_options or {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LSPServerConfig":
        """Create config from dictionary (supports camelCase keys)."""
        return cls(
            command=data["command"],
            args=data.get("args", []),
            env=data.get("env", {}),
            extension_to_language=data.get("extensionToLanguage")
            or data.get("extension_to_language", {}),
            initialization_options=data.get("initializationOptions")
            or data.get("initialization_options", {}),
        )


class LSPServerManager:
    """Manages multiple LSP servers based on file extension routing.

    This class handles:
    - Starting LSP servers on-demand based on file extension
    - Routing files to the appropriate server
    - Tracking open documents per server
    - Cleaning up servers on shutdown
    """

    def __init__(self, config: dict[str, Any], workspace_root: str):
        """Initialize the server manager.

        Args:
            config: LSP configuration dictionary with format:
                {"servers": {"name": {"command": "...", "args": [...], ...}}}
                or {"lspServers": {"name": {...}}}
            workspace_root: Root directory of the workspace
        """
        self._workspace_root = workspace_root
        self._clients: dict[str, LSPClient] = {}
        self._server_configs: dict[str, LSPServerConfig] = {}
        self._extension_to_server: dict[str, str] = {}
        # uri -> (server_name, version)
        self._open_documents: dict[str, tuple[str, int]] = {}
        self._lock = threading.Lock()

        self._parse_config(config)

    def _parse_config(self, config: dict[str, Any]) -> None:
        """Parse LSP configuration and build routing table."""
        # Support both "servers" and "lspServers" keys
        servers = config.get("servers") or config.get("lspServers") or {}

        for name, server_data in servers.items():
            server_config = LSPServerConfig.from_dict(server_data)
            self._server_configs[name] = server_config

            # Build extension to server mapping
            for ext, lang_id in server_config.extension_to_language.items():
                # Normalize extension to include dot
                ext_normalized = ext if ext.startswith(".") else f".{ext}"
                self._extension_to_server[ext_normalized] = name
                logger.debug(f"Registered LSP: {ext_normalized} -> {name}")

    def get_server_for_file(self, file_path: str) -> str | None:
        """Determine which server should handle a file.

        Args:
            file_path: Path to the file

        Returns:
            Server name or None if no server handles this file type
        """
        ext = Path(file_path).suffix.lower()
        return self._extension_to_server.get(ext)

    def get_language_id(self, file_path: str) -> str | None:
        """Get the language ID for a file.

        Args:
            file_path: Path to the file

        Returns:
            Language ID or None if no server handles this file type
        """
        ext = Path(file_path).suffix.lower()
        server_name = self._extension_to_server.get(ext)
        if server_name is None:
            return None

        config = self._server_configs.get(server_name)
        if config is None:
            return None

        return config.extension_to_language.get(ext)

    def get_client_for_file(self, file_path: str) -> LSPClient | None:
        """Get or start the appropriate LSP client for a file.

        Args:
            file_path: Path to the file

        Returns:
            LSPClient or None if no server handles this file type
        """
        server_name = self.get_server_for_file(file_path)
        if server_name is None:
            return None

        return self._ensure_client(server_name)

    def _ensure_client(self, server_name: str) -> LSPClient:
        """Ensure a client is running for the given server.

        Args:
            server_name: Name of the LSP server

        Returns:
            Running LSPClient instance
        """
        with self._lock:
            if server_name in self._clients:
                client = self._clients[server_name]
                if client.is_running:
                    return client
                # Client exists but not running, remove it
                del self._clients[server_name]

            # Start new client
            config = self._server_configs[server_name]
            client = LSPClient(
                command=config.command,
                args=config.args,
                workspace_root=self._workspace_root,
                env=config.env,
                initialization_options=config.initialization_options,
            )
            client.start_sync()
            self._clients[server_name] = client
            logger.info(f"Started LSP server: {server_name}")
            return client

    def ensure_document_open(
        self, file_path: str
    ) -> tuple[LSPClient | None, str | None]:
        """Ensure a document is open in the appropriate LSP server.

        Args:
            file_path: Path to the file

        Returns:
            Tuple of (client, language_id) or (None, None) if no server
        """
        server_name = self.get_server_for_file(file_path)
        if server_name is None:
            return None, None

        language_id = self.get_language_id(file_path)
        if language_id is None:
            return None, None

        client = self._ensure_client(server_name)
        uri = Path(file_path).as_uri()

        with self._lock:
            if uri in self._open_documents:
                # Document already open
                return client, language_id

            # Read file content and open it
            try:
                content = Path(file_path).read_text()
            except Exception as e:
                logger.warning(f"Failed to read file for LSP: {file_path}: {e}")
                return client, language_id

            # Open document in server
            version = 1
            client.call_sync(
                client.text_document_did_open(uri, language_id, version, content)
            )
            self._open_documents[uri] = (server_name, version)
            logger.debug(f"Opened document in LSP: {file_path}")

        return client, language_id

    def close_document(self, file_path: str) -> None:
        """Close a document in the LSP server.

        Args:
            file_path: Path to the file
        """
        uri = Path(file_path).as_uri()

        with self._lock:
            if uri not in self._open_documents:
                return

            server_name, _ = self._open_documents.pop(uri)
            client = self._clients.get(server_name)
            if client and client.is_running:
                try:
                    client.call_sync(client.text_document_did_close(uri))
                    logger.debug(f"Closed document in LSP: {file_path}")
                except Exception as e:
                    logger.debug(f"Error closing document: {e}")

    def refresh_document(self, file_path: str) -> None:
        """Refresh a document's content in the LSP server.

        Call this after file modifications to update the server's view.

        Args:
            file_path: Path to the file
        """
        uri = Path(file_path).as_uri()

        with self._lock:
            if uri not in self._open_documents:
                return

            server_name, version = self._open_documents[uri]
            client = self._clients.get(server_name)
            if not client or not client.is_running:
                return

            try:
                content = Path(file_path).read_text()
                new_version = version + 1
                client.call_sync(
                    client.text_document_did_change(uri, new_version, content)
                )
                self._open_documents[uri] = (server_name, new_version)
                logger.debug(f"Refreshed document in LSP: {file_path}")
            except Exception as e:
                logger.debug(f"Error refreshing document: {e}")

    def close_server(self, server_name: str) -> None:
        """Stop a specific LSP server.

        Args:
            server_name: Name of the server to stop
        """
        with self._lock:
            client = self._clients.pop(server_name, None)
            if client:
                # Close all documents for this server
                docs_to_remove = [
                    uri
                    for uri, (name, _) in self._open_documents.items()
                    if name == server_name
                ]
                for uri in docs_to_remove:
                    self._open_documents.pop(uri, None)

                try:
                    client.sync_close()
                except Exception as e:
                    logger.debug(f"Error closing LSP server {server_name}: {e}")

    def close_all(self) -> None:
        """Shutdown all running LSP servers."""
        with self._lock:
            server_names = list(self._clients.keys())

        for server_name in server_names:
            self.close_server(server_name)

        logger.info("All LSP servers stopped")

    @property
    def running_servers(self) -> list[str]:
        """Get list of currently running server names."""
        with self._lock:
            return [
                name for name, client in self._clients.items() if client.is_running
            ]

    @property
    def open_document_count(self) -> int:
        """Get count of open documents across all servers."""
        with self._lock:
            return len(self._open_documents)


def create_server_manager(
    config: dict[str, Any], workspace_root: str
) -> LSPServerManager | None:
    """Create an LSP server manager from configuration.

    Args:
        config: LSP configuration dictionary
        workspace_root: Root directory of the workspace

    Returns:
        LSPServerManager or None if no servers configured
    """
    servers = config.get("servers") or config.get("lspServers") or {}
    if not servers:
        return None

    return LSPServerManager(config, workspace_root)
