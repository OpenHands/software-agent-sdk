"""Live test: agent server bind host guard behavior."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _can_connect(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _non_loopback_ip() -> str | None:
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, 80, proto=socket.IPPROTO_TCP):
            ip = info[4][0]
            if ip not in ("127.0.0.1", "::1") and not ip.startswith("169.254"):
                return ip
    except Exception:
        return None
    return None


def run_main(env: dict, extra_args: list[str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "openhands.agent_server"] + extra_args
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=8)


def main():
    base_env = dict(os.environ)
    clean_env = {
        k: v
        for k, v in base_env.items()
        if "SESSION_API_KEY" not in k and "OH_SESSION" not in k
    }
    cfg = Path("/tmp/__noconfig_bind_test.json")
    cfg.write_text("{}")
    clean_env["OPENHANDS_AGENT_SERVER_CONFIG_PATH"] = str(cfg)

    non_loopback = _non_loopback_ip()
    results: dict = {"non_loopback_ip": non_loopback}

    # 1. No key, explicit --host 0.0.0.0 -> allowed but warned, binds 0.0.0.0
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "openhands.agent_server",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ],
        env=clean_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(6)
    loopback_up_e = _can_connect("127.0.0.1", port)
    non_loopback_up_e = _can_connect(non_loopback, port) if non_loopback else None
    proc.terminate()
    try:
        out_e, _ = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        out_e, _ = proc.communicate()
    results["no_key_explicit_wildcard"] = {
        "loopback_up": loopback_up_e,
        "non_loopback_up": non_loopback_up_e,
        "bind_line": [
            line for line in out_e.splitlines() if "Uvicorn running on" in line
        ],
        "warn_line": [
            line for line in out_e.splitlines() if "without a session API key" in line
        ],
    }

    # 2. No key, default host -> should bind loopback only
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "openhands.agent_server", "--port", str(port)],
        env=clean_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(5)
    loopback_up = _can_connect("127.0.0.1", port)
    non_loopback_up = _can_connect(non_loopback, port) if non_loopback else None
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    results["no_key_default"] = {
        "loopback_up": loopback_up,
        "non_loopback_up": non_loopback_up,
        "bind_line": [
            line for line in out.splitlines() if "Uvicorn running on" in line
        ],
    }

    # 3. With key, default host -> should bind 0.0.0.0
    key_env = dict(clean_env)
    key_env["SESSION_API_KEY"] = "test-secret-key-123"
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "openhands.agent_server", "--port", str(port)],
        env=key_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(5)
    loopback_up_k = _can_connect("127.0.0.1", port)
    non_loopback_up_k = _can_connect(non_loopback, port) if non_loopback else None
    proc.terminate()
    try:
        out_k, _ = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        out_k, _ = proc.communicate()
    results["with_key_default"] = {
        "loopback_up": loopback_up_k,
        "non_loopback_up": non_loopback_up_k,
        "bind_line": [
            line for line in out_k.splitlines() if "Uvicorn running on" in line
        ],
    }

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
