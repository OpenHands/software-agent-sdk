"""Virtualization-free Smol SDK fixture, loaded only in the owned helper."""

import json
from pathlib import Path

import pytest


_FAKE_SMOL = """
import json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

MachineConfig = ResourceSpec = ConnectOptions = PortSpec = SimpleNamespace
MODE = os.environ.get("SMOL_TEST_MODE", "")

def record(event, **data):
    with open(os.environ["SMOL_TEST_STATE"], "a") as f:
        f.write(json.dumps(dict(event=event, **data)) + "\\n")

def local_availability():
    record("availability")
    if MODE == "unavailable": return (False, "KVM_UNAVAILABLE", "test")
    return (True, None, None)

class Machine:
    @classmethod
    def create(cls, config, connection):
        record("create", name=config.name, image=config.image,
               resources=vars(config.resources), command=config.command,
               env=config.env, workdir=config.workdir, persistent=config.persistent,
               detach=config.detach, target=connection.target)
        assert connection.target == "local"
        if MODE == "hang_create_child":
            import subprocess
            child = subprocess.Popen(["/bin/sleep", "30"])
            record("child", pid=child.pid)
            time.sleep(30)
        if MODE == "hang_create": time.sleep(30)
        if MODE == "create_interrupt": raise KeyboardInterrupt()
        if MODE in ("create_error", "create_cleanup_error"):
            raise RuntimeError("do-not-log-test-secret")
        self = cls()
        self.name = config.name
        env = config.env
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                runtime = "docker" if MODE == "wrong_runtime" else "local"
                self.respond(503 if MODE == "health_fail" else 200,
                             {"version": "1.60.1", "conversation_runtime": runtime})
            def do_POST(self):
                key = self.headers.get("X-Session-API-Key")
                auth = key == env["OH_SESSION_API_KEYS_0"] or MODE == "auth_disabled"
                self.respond(200 if auth and MODE != "auth_fail" else 401,
                             {"exit_code": 0, "stdout": "ready", "stderr": ""})
            def respond(self, status, body):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def publish_atomic(self, guest): record("publish", guest=guest)
    def endpoint(self, guest):
        host = "192.0.2.1" if MODE == "nonloopback" else "127.0.0.1"
        return SimpleNamespace(http_url=f"http://{host}:{self.server.server_port}")
    def list_images(self):
        return [SimpleNamespace(reference="test:server", digest="sha256:" + "1" * 64)]
    def stop(self, **kwargs): record("stop", name=self.name)
    @staticmethod
    def delete_by_name(name, conn, drain_secs=0):
        record("delete", name=name, target=conn.target)
        if MODE == "hang_cleanup": time.sleep(30)
        if MODE in ("cleanup_error", "create_cleanup_error"):
            raise RuntimeError("do-not-log-test-secret")
"""


@pytest.fixture
def smol_fake_sdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = tmp_path / "smol.py"
    module.write_text(_FAKE_SMOL)
    metadata = tmp_path / "smolmachines-1.32.4.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: smolmachines\nVersion: 1.32.4\n")
    state = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("SMOL_TEST_STATE", str(state))
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())

    def calls():
        if not state.exists():
            return []
        return [json.loads(line) for line in state.read_text().splitlines()]

    return calls
