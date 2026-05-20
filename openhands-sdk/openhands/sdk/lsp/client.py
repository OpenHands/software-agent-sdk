"""LSP Client for communicating with Language Server Protocol servers."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from openhands.sdk.logger import get_logger
from openhands.sdk.lsp.exceptions import (
    LSPConnectionError,
    LSPServerError,
    LSPTimeoutError,
)
from openhands.sdk.utils.async_executor import AsyncExecutor


logger = get_logger(__name__)


class LSPClient:
    """Manages a single LSP server process and JSON-RPC communication.

    Uses AsyncExecutor for sync/async bridging, following the MCP client pattern.
    Communicates with LSP servers via stdio using JSON-RPC 2.0 protocol.
    """

    def __init__(
        self,
        command: str,
        args: list[str],
        workspace_root: str,
        env: dict[str, str] | None = None,
        initialization_options: dict[str, Any] | None = None,
    ):
        """Initialize LSP client.

        Args:
            command: Command to run the LSP server (e.g., "typescript-language-server")
            args: Arguments for the command (e.g., ["--stdio"])
            workspace_root: Root directory of the workspace
            env: Additional environment variables for the server process
            initialization_options: Options passed to server during initialization
        """
        self._command = command
        self._args = args
        self._workspace_root = workspace_root
        self._env = env or {}
        self._initialization_options = initialization_options or {}
        self._executor = AsyncExecutor()
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending_requests: dict[int, asyncio.Future[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._initialized = False
        self._capabilities: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """Check if the LSP server process is running."""
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Start the LSP server process and perform initialize handshake."""
        if self.is_running:
            logger.debug(f"LSP server already running: {self._command}")
            return

        # Prepare environment
        env = os.environ.copy()
        env.update(self._env)

        try:
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            logger.info(f"Started LSP server: {self._command} {' '.join(self._args)}")
        except FileNotFoundError as e:
            raise LSPConnectionError(
                f"LSP server command not found: {self._command}. "
                "Make sure the language server is installed.",
                server_name=self._command,
            ) from e
        except Exception as e:
            raise LSPConnectionError(
                f"Failed to start LSP server: {e}",
                server_name=self._command,
            ) from e

        # Start reader task to handle incoming messages
        self._reader_task = asyncio.create_task(self._read_messages())

        # Perform LSP initialize handshake
        await self._initialize()

    async def _initialize(self) -> None:
        """Perform LSP initialize/initialized handshake."""
        workspace_uri = Path(self._workspace_root).as_uri()

        init_params = {
            "processId": os.getpid(),
            "rootUri": workspace_uri,
            "rootPath": self._workspace_root,
            "capabilities": {
                "textDocument": {
                    "synchronization": {
                        "dynamicRegistration": False,
                        "willSave": False,
                        "willSaveWaitUntil": False,
                        "didSave": True,
                    },
                    "completion": {
                        "dynamicRegistration": False,
                        "completionItem": {
                            "snippetSupport": False,
                            "documentationFormat": ["plaintext", "markdown"],
                        },
                    },
                    "hover": {
                        "dynamicRegistration": False,
                        "contentFormat": ["plaintext", "markdown"],
                    },
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "documentSymbol": {"dynamicRegistration": False},
                },
                "workspace": {
                    "workspaceFolders": True,
                },
            },
            "workspaceFolders": [
                {
                    "uri": workspace_uri,
                    "name": Path(self._workspace_root).name,
                }
            ],
        }

        if self._initialization_options:
            init_params["initializationOptions"] = self._initialization_options

        result = await self._send_request("initialize", init_params)
        self._capabilities = result.get("capabilities", {})

        # Send initialized notification
        await self._send_notification("initialized", {})
        self._initialized = True
        logger.debug(f"LSP server initialized: {self._command}")

    async def stop(self) -> None:
        """Send shutdown/exit and terminate the server process."""
        if not self.is_running:
            return

        try:
            # Send shutdown request
            await self._send_request("shutdown", None, timeout=5.0)
            # Send exit notification
            await self._send_notification("exit", None)
        except Exception as e:
            logger.debug(f"Error during LSP shutdown: {e}")

        # Terminate process
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
            except Exception as e:
                logger.debug(f"Error terminating LSP process: {e}")

        # Cancel reader task
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        self._process = None
        self._reader_task = None
        self._initialized = False
        logger.info(f"LSP server stopped: {self._command}")

    async def _read_messages(self) -> None:
        """Read and dispatch messages from the LSP server."""
        assert self._process is not None
        assert self._process.stdout is not None

        while self.is_running:
            try:
                # Read Content-Length header
                header = await self._process.stdout.readline()
                if not header:
                    break

                header_str = header.decode("utf-8").strip()
                if not header_str.startswith("Content-Length:"):
                    continue

                content_length = int(header_str.split(":")[1].strip())

                # Read empty line after headers
                await self._process.stdout.readline()

                # Read content
                content = await self._process.stdout.readexactly(content_length)
                message = json.loads(content.decode("utf-8"))

                # Dispatch message
                await self._handle_message(message)

            except asyncio.CancelledError:
                break
            except asyncio.IncompleteReadError:
                break
            except Exception as e:
                logger.debug(f"Error reading LSP message: {e}")
                break

    async def _handle_message(self, message: dict[str, Any]) -> None:
        """Handle incoming LSP message."""
        if "id" in message and "method" not in message:
            # Response to a request
            request_id = message["id"]
            if request_id in self._pending_requests:
                future = self._pending_requests.pop(request_id)
                if "error" in message:
                    error = message["error"]
                    future.set_exception(
                        LSPServerError(
                            message=error.get("message", "Unknown error"),
                            code=error.get("code", -1),
                            data=error.get("data"),
                        )
                    )
                else:
                    future.set_result(message.get("result"))
        elif "method" in message:
            # Server notification or request
            method = message["method"]
            logger.debug(f"Received LSP notification: {method}")
            # Handle server-initiated requests if needed
            if "id" in message:
                # Server request - send empty response for now
                await self._send_response(message["id"], None)

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None,
        timeout: float = 30.0,
    ) -> Any:
        """Send a JSON-RPC request and wait for response."""
        if not self.is_running:
            raise LSPConnectionError(
                "LSP server is not running", server_name=self._command
            )

        async with self._lock:
            self._request_id += 1
            request_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future

        await self._write_message(message)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise LSPTimeoutError(
                f"LSP request '{method}' timed out after {timeout}s",
                timeout=timeout,
                server_name=self._command,
                operation=method,
            ) from None

    async def _send_notification(
        self, method: str, params: dict[str, Any] | None
    ) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self.is_running:
            return

        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            message["params"] = params

        await self._write_message(message)

    async def _send_response(self, request_id: int, result: Any) -> None:
        """Send a JSON-RPC response."""
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }
        await self._write_message(message)

    async def _write_message(self, message: dict[str, Any]) -> None:
        """Write a JSON-RPC message to the server."""
        assert self._process is not None
        assert self._process.stdin is not None

        content = json.dumps(message)
        content_bytes = content.encode("utf-8")

        header = f"Content-Length: {len(content_bytes)}\r\n\r\n"
        header_bytes = header.encode("utf-8")

        self._process.stdin.write(header_bytes + content_bytes)
        await self._process.stdin.drain()

    # LSP Protocol Methods

    async def text_document_definition(
        self, uri: str, line: int, character: int
    ) -> list[dict[str, Any]]:
        """Send textDocument/definition request.

        Args:
            uri: Document URI (file:// format)
            line: Line number (0-indexed)
            character: Character position (0-indexed)

        Returns:
            List of location objects with uri, range
        """
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        }
        result = await self._send_request("textDocument/definition", params)
        return self._normalize_locations(result)

    async def text_document_references(
        self, uri: str, line: int, character: int, include_declaration: bool = True
    ) -> list[dict[str, Any]]:
        """Send textDocument/references request.

        Args:
            uri: Document URI (file:// format)
            line: Line number (0-indexed)
            character: Character position (0-indexed)
            include_declaration: Include the declaration location

        Returns:
            List of location objects with uri, range
        """
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration},
        }
        result = await self._send_request("textDocument/references", params)
        return self._normalize_locations(result)

    async def text_document_hover(
        self, uri: str, line: int, character: int
    ) -> dict[str, Any] | None:
        """Send textDocument/hover request.

        Args:
            uri: Document URI (file:// format)
            line: Line number (0-indexed)
            character: Character position (0-indexed)

        Returns:
            Hover object with contents and optional range, or None
        """
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        }
        return await self._send_request("textDocument/hover", params)

    async def text_document_did_open(
        self, uri: str, language_id: str, version: int, text: str
    ) -> None:
        """Send textDocument/didOpen notification.

        Args:
            uri: Document URI (file:// format)
            language_id: Language identifier (e.g., "typescript", "python")
            version: Document version
            text: Full text content of the document
        """
        params = {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": version,
                "text": text,
            }
        }
        await self._send_notification("textDocument/didOpen", params)

    async def text_document_did_close(self, uri: str) -> None:
        """Send textDocument/didClose notification.

        Args:
            uri: Document URI (file:// format)
        """
        params = {"textDocument": {"uri": uri}}
        await self._send_notification("textDocument/didClose", params)

    async def text_document_did_change(
        self, uri: str, version: int, text: str
    ) -> None:
        """Send textDocument/didChange notification (full sync).

        Args:
            uri: Document URI (file:// format)
            version: Document version (incremented)
            text: Full text content of the document
        """
        params = {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": [{"text": text}],
        }
        await self._send_notification("textDocument/didChange", params)

    def _normalize_locations(
        self, result: Any
    ) -> list[dict[str, Any]]:
        """Normalize location result to list format."""
        if result is None:
            return []
        if isinstance(result, dict):
            return [result]
        if isinstance(result, list):
            return result
        return []

    # Sync wrappers

    def call_sync(
        self, coro: Any, timeout: float = 30.0
    ) -> Any:
        """Execute async method from sync context using AsyncExecutor."""
        return self._executor.run_async(coro, timeout=timeout)

    def start_sync(self) -> None:
        """Synchronously start the LSP server."""
        self.call_sync(self.start())

    def stop_sync(self) -> None:
        """Synchronously stop the LSP server."""
        try:
            self.call_sync(self.stop(), timeout=10.0)
        except Exception as e:
            logger.debug(f"Error during sync stop: {e}")

    def sync_close(self) -> None:
        """Synchronously close the client and cleanup resources."""
        self.stop_sync()
        self._executor.close()

    def __del__(self) -> None:
        """Cleanup on deletion."""
        try:
            self.sync_close()
        except Exception:
            pass
