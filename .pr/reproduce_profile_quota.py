"""Exercise real profile-validation HTTP with a deterministic provider."""

import argparse
import importlib.metadata
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checkout = Path.cwd()
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, cwd=checkout
    ).strip()
    request_codes = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            code = (
                "insufficient_quota" if "quota" in self.path else "rate_limit_exceeded"
            )
            request_codes.append(code)
            data = json.dumps(
                {
                    "error": {
                        "message": (
                            "You exceeded your current quota, "
                            "please check your plan and billing details."
                        ),
                        "type": code,
                        "code": code,
                    }
                }
            ).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    result = {
        "commit": commit,
        "python": sys.version.split()[0],
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("openhands-sdk", "openhands-agent-server", "litellm", "openai")
        },
    }
    with tempfile.TemporaryDirectory(prefix="sdk40-repro-") as temp:
        state = Path(temp)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            name: os.environ[name]
            for name in ("PATH", "LANG", "TMPDIR")
            if name in os.environ
        }
        env.update(
            {
                "OH_PERSISTENCE_DIR": str(state / "persist"),
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                "OPENHANDS_BUILD_GIT_SHA": commit,
            }
        )
        command = [
            sys.executable,
            "-c",
            (
                "import uvicorn; from openhands.agent_server.api import create_app; "
                "from openhands.agent_server.config import Config; "
                "uvicorn.run(create_app(Config(static_files_path=None, "
                "session_api_keys=[], "
                "secret_key=None, enable_vscode=False, preload_tools=False)), "
                f"host='127.0.0.1', port={port}, log_level='warning')"
            ),
        ]
        log_path = args.output.with_suffix(".server.log")
        with log_path.open("w") as log:
            proc = subprocess.Popen(command, cwd=state, env=env, stdout=log, stderr=log)
            base = f"http://127.0.0.1:{port}"

            def request(path, data=None):
                payload = None if data is None else json.dumps(data).encode()
                req = urllib.request.Request(
                    base + path,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=20) as response:
                    return {"status": response.status, "body": json.load(response)}

            try:
                for _ in range(150):
                    if proc.poll() is not None:
                        raise RuntimeError(f"server exited; inspect {log_path}")
                    try:
                        request("/health")
                        break
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.2)
                else:
                    raise RuntimeError(
                        f"server did not become healthy; inspect {log_path}"
                    )
                before = request("/api/profiles")
                results = []
                for kind in ("transient", "quota"):
                    payload = {
                        "llm": {
                            "model": "openai/gpt-4o",
                            "base_url": f"http://127.0.0.1:{provider.server_port}/{kind}/v1",
                            "api_key": "fabricated-local-test-key",
                            "num_retries": 0,
                        }
                    }
                    started = time.monotonic()
                    response = request("/api/profiles/qa-quota/validate", payload)
                    results.append(
                        {
                            "kind": kind,
                            "response": response,
                            "seconds": round(time.monotonic() - started, 3),
                        }
                    )
                result.update(
                    {
                        "results": results,
                        "profile_store_unchanged": before == request("/api/profiles"),
                        "provider_requests": request_codes,
                    }
                )
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                result["server_stopped"] = proc.poll() is not None
        with socket.socket() as sock:
            result["server_port_closed"] = sock.connect_ex(("127.0.0.1", port)) != 0
    provider.shutdown()
    provider.server_close()
    thread.join()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
