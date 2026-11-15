"""Conversation-owned, same-user capability broker; not a security sandbox."""

import hmac
import json
import os
import secrets
import shlex
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.secret.terminal_credentials import TerminalCredentialBinding
from openhands.tools.terminal import credential_adapter


class TerminalCredentialRuntime:
    def __init__(
        self,
        bindings: tuple[TerminalCredentialBinding, ...],
        registry: SecretRegistry,
        *,
        executable_path: str | None = None,
    ) -> None:
        self.bindings = bindings
        self.registry = registry
        self._temporary = tempfile.TemporaryDirectory(prefix="openhands-credentials-")
        self.directory = Path(self._temporary.name)
        self._closed = False
        search_path = os.pathsep.join(
            entry
            for entry in (executable_path or os.environ.get("PATH", "")).split(
                os.pathsep
            )
            if Path(entry).is_absolute()
        )
        executables = {
            consumer: shutil.which(consumer, path=search_path)
            for consumer in {"git", *(b.consumer for b in bindings)}
        }
        capability = secrets.token_urlsafe(32)
        authorized = {(b.consumer, b.host): b for b in bindings}
        self_runtime = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(5)

            def do_POST(self) -> None:
                if not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + capability
                ):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError("invalid request size")
                    query = json.loads(self.rfile.read(length))
                    binding = authorized.get((query["consumer"], query["host"]))
                    result = {}
                    if binding is not None:
                        token = registry.get_secret_value(binding.secret_ref)
                        if (
                            token
                            and not self_runtime._closed
                            and not any(c in token for c in "\r\n\0")
                        ):
                            result = {"token": token, "username": binding.username}
                    payload = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except Exception:
                    self.send_error(400, "Credential request failed")

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except BaseException:
            self._temporary.cleanup()
            raise
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        try:
            if getattr(sys, "frozen", False):
                launcher = [sys.executable, "--terminal-credential-adapter"]
            else:
                adapter = self.directory / "adapter.py"
                adapter.write_bytes(Path(credential_adapter.__file__).read_bytes())
                launcher = [sys.executable, "-I", str(adapter)]
            config_path = self.directory / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "url": f"http://127.0.0.1:{self._server.server_port}/",
                        "capability": capability,
                        "git_hosts": [b.host for b in bindings if b.consumer == "git"],
                        "launcher": launcher,
                        "executables": executables,
                    }
                )
            )
            config_path.chmod(0o600)
            for consumer in {b.consumer for b in bindings}:
                wrapper = self.directory / consumer
                wrapper.write_text(
                    "#!/bin/sh\nexec "
                    + shlex.join(launcher + [str(config_path), consumer])
                    + ' "$@"\n'
                )
                wrapper.chmod(0o700)
                if os.name == "nt":

                    def quote(value: str) -> str:
                        return "'" + value.replace("'", "''") + "'"

                    wrapper.with_suffix(".ps1").write_text(
                        "& "
                        + " ".join(
                            quote(v) for v in launcher + [str(config_path), consumer]
                        )
                        + " @args\nexit $LASTEXITCODE\n"
                    )
            self._thread.start()
        except BaseException:
            self._server.server_close()
            self._temporary.cleanup()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._temporary.cleanup()
