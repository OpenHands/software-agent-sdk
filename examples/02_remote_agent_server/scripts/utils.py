"""Helpers for examples that run a local OpenHands agent-server subprocess."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from typing import TextIO

import httpx


def _stream_output(stream: TextIO, prefix: str, target_stream: TextIO) -> None:
    try:
        for line in iter(stream.readline, ""):
            if line:
                target_stream.write(f"[{prefix}] {line}")
                target_stream.flush()
    except Exception as e:
        print(f"Error streaming {prefix}: {e}", file=sys.stderr)
    finally:
        stream.close()


class ManagedAPIServer:
    """Subprocess ``openhands.agent_server``: wait for ``/health``, stop on exit.

    Pass ``extra_env`` for defaults such as ``OH_SECRET_KEY`` (``os.environ`` still
    wins if those variables are set in the environment). With
    ``use_session_api_key=True``, ``SESSION_API_KEY`` is generated; read it from
    ``session_api_key`` for the ``X-Session-API-Key`` header.
    """

    def __init__(
        self,
        port: int = 8000,
        host: str = "127.0.0.1",
        *,
        extra_env: Mapping[str, str] | None = None,
        use_session_api_key: bool = False,
        health_request_timeout: float = 1.0,
        max_start_wait_seconds: int = 30,
    ) -> None:
        self.port = port
        self.host = host
        self.base_url = f"http://{host}:{port}"
        self.process: subprocess.Popen[str] | None = None
        if use_session_api_key:
            self.session_api_key: str | None = secrets.token_urlsafe(32)
        else:
            self.session_api_key = None
        self._extra_env = dict(extra_env) if extra_env else {}
        self._health_request_timeout = health_request_timeout
        self._max_start_wait_seconds = max_start_wait_seconds

    def __enter__(self) -> ManagedAPIServer:
        print(f"Starting OpenHands API server on {self.base_url}...")
        if self.session_api_key is not None:
            print("Session API key is set; send it as X-Session-API-Key on API calls.")

        # Same precedence as the old inlined examples: os.environ overwrites demo keys.
        env_keys: dict[str, str] = {"LOG_JSON": "true", **self._extra_env}
        if self.session_api_key is not None:
            env_keys["SESSION_API_KEY"] = self.session_api_key
        env = {**env_keys, **os.environ}

        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "openhands.agent_server",
                "--port",
                str(self.port),
                "--host",
                self.host,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        proc = self.process
        assert proc.stdout is not None
        assert proc.stderr is not None
        threading.Thread(
            target=_stream_output,
            args=(proc.stdout, "SERVER", sys.stdout),
            daemon=True,
        ).start()
        threading.Thread(
            target=_stream_output,
            args=(proc.stderr, "SERVER", sys.stderr),
            daemon=True,
        ).start()

        for _ in range(self._max_start_wait_seconds):
            try:
                response = httpx.get(
                    f"{self.base_url}/health",
                    timeout=self._health_request_timeout,
                )
                if response.status_code == 200:
                    print(f"API server is ready at {self.base_url}")
                    return self
            except httpx.RequestError:
                pass

            if proc.poll() is not None:
                raise RuntimeError(
                    "Server process exited before becoming healthy. "
                    "Check the server logs above."
                )
            time.sleep(1)

        raise RuntimeError(
            f"Server failed to start after {self._max_start_wait_seconds} seconds"
        )

    def __exit__(self, *_exc: object) -> None:
        if self.process is None:
            return
        print("Stopping API server...")
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        time.sleep(0.5)
        print("API server stopped.")
