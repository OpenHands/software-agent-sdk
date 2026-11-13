"""Live probes for PodmanWorkspace: auth, loopback isolation, failure paths.

Run from the repository root:  uv run python .pr/podman_probes.py
Prints each command and its result. The session key is shown as <key>.
"""

import http.server
import os
import secrets
import socket
import stat
import subprocess
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from openhands.workspace import PodmanWorkspace


IMAGE = "ghcr.io/openhands/agent-server:latest-python"
KEY = secrets.token_hex(16)


def show(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    print("$", " ".join(cmd).replace(KEY, "<key>"))
    out = (proc.stdout + proc.stderr).replace(KEY, "<key>").rstrip()
    print(out if out else "(no output)", f"[exit {proc.returncode}]")
    return proc


def leftovers() -> None:
    show(["podman", "ps", "--all", "--filter", "name=agent-server", "--format",
          "{{.Names}} {{.Status}}"])
    show(["pgrep", "-af", "podman logs --follow"])


def heading(text: str) -> None:
    print(f"\n### {text}\n", flush=True)


def probe_auth_and_isolation(project: Path) -> None:
    heading("Authentication, key exposure, loopback isolation")
    os.environ["OH_SESSION_API_KEYS_0"] = KEY

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler
    )
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Host HTTP server on 127.0.0.1:{port}")

    with PodmanWorkspace(
        volumes=[f"{project}:/workspace/project"],
        working_dir="/workspace/project",
    ) as ws:
        url = f"{ws.host}/api/conversations/count"
        for header in [None, "X-Session-API-Key: wrong", f"X-Session-API-Key: {KEY}"]:
            cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}\\n", url]
            show(cmd + (["-H", header] if header else []))

        print("Processes whose argv contains the key:")
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            if any(KEY.encode() in a for a in argv):
                print(" ", pid, argv[0].decode())

        probe = (
            "id -u; echo w > new.txt; ls /home; env | grep -c SESSION_API_KEY; "
            f"for h in 127.0.0.1 host.containers.internal; do "
            f"curl -s -m 3 -o /dev/null -w \"$h %{{http_code}}\\n\" http://$h:{port}/"
            " || echo \"$h unreachable (curl exit $?)\"; done"
        )
        result = ws.execute_command(probe, cwd="/workspace/project")
        print(f"agent shell: {probe}")
        print(result.stdout.rstrip(), f"[exit {result.exit_code}]")
        show(["stat", "-c", "%n %u:%g", str(project / "new.txt")])
        print("While running:")
        show(["pgrep", "-af", "podman logs --follow"])

    server.shutdown()
    del os.environ["OH_SESSION_API_KEYS_0"]
    print("After the with block:")
    leftovers()


def probe_vscode_port() -> None:
    heading("extra_ports=True publishes the VSCode port on loopback")
    with PodmanWorkspace(extra_ports=True, detach_logs=False) as ws:
        assert ws.host_port is not None
        vscode = ws.host_port + 1
        show(["podman", "port", "--all"])
        show(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}\\n", "--retry",
              "10", "--retry-connrefused", "--retry-delay", "1",
              f"http://127.0.0.1:{vscode}/"])
    leftovers()


def expect_failure(label: str, **kwargs) -> None:
    heading(label)
    try:
        PodmanWorkspace(**kwargs)
    except Exception as exc:
        text = str(exc).strip().splitlines()
        print(f"{type(exc).__name__}: " + "\n  ".join(text[:6]))
        for note in getattr(exc, "__notes__", []):
            print(f"note: {note.strip()}")
        print("While the exception is alive:")
        leftovers()
    else:
        print("UNEXPECTED: workspace started")


def build(tag: str, entrypoint: str, ctx: Path) -> None:
    (ctx / "Containerfile").write_text(f"FROM {IMAGE}\nENTRYPOINT {entrypoint}\n")
    subprocess.run(["podman", "build", "-q", "-t", tag, str(ctx)], check=True,
                   capture_output=True)
    print(f"{tag}: ENTRYPOINT {entrypoint}")


def probe_failures(tmp: Path) -> None:
    heading("Test images")
    build("localhost/probe-crash", '["sh", "-c", "echo probe: bad config >&2; exit 3"]',
          tmp)
    build("localhost/probe-hang", '["sh", "-c", "sleep infinity", "sh"]', tmp)

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        with patch("openhands.workspace.podman.workspace.check_port_available",
                   return_value=True):
            expect_failure(
                f"Port {port} taken after the availability check",
                host_port=port,
            )

    expect_failure("Volume source does not exist",
                   volumes=[f"{tmp}/missing:/workspace/project"])
    expect_failure("Entrypoint exits during startup",
                   server_image="localhost/probe-crash")
    expect_failure("Server never becomes healthy (health_check_timeout=5)",
                   server_image="localhost/probe-hang", health_check_timeout=5)


def probe_failed_removal(tmp: Path) -> None:
    heading("podman rm fails once (PATH shim around the real podman)")
    shim_dir = tmp / "shim"
    shim_dir.mkdir()
    marker = tmp / "rm-failed"
    shim = shim_dir / "podman"
    shim.write_text(
        "#!/bin/sh\n"
        f'if [ \"$1\" = rm ] && [ ! -e {marker} ]; then\n'
        f"  touch {marker}; echo 'Error: injected rm failure' >&2; exit 125\n"
        "fi\n"
        'exec /usr/bin/podman "$@"\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    os.environ["PATH"] = f"{shim_dir}:{os.environ['PATH']}"
    try:
        expect_failure(
            "Health timeout, then the rollback's podman rm fails",
            server_image="localhost/probe-hang", health_check_timeout=5,
        )
        print("After the exception is released (__del__ retries):")
        leftovers()

        heading("cleanup() on a running workspace: removal fails once, then retry")
        marker.unlink()
        ws = PodmanWorkspace()
        try:
            ws.cleanup()
        except RuntimeError as exc:
            print(f"first cleanup(): RuntimeError: {str(exc).strip()}")
        leftovers()
        ws.cleanup()
        print("second cleanup(): returned")
        leftovers()
    finally:
        os.environ["PATH"] = os.environ["PATH"].split(":", 1)[1]


def main() -> None:
    show(["podman", "--version"])
    show(["podman", "image", "inspect", "--format", "{{.Id}}", IMAGE])
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        project = tmp_path / "project"
        project.mkdir()
        probe_auth_and_isolation(project)
        probe_vscode_port()
        probe_failures(tmp_path)
        probe_failed_removal(tmp_path)


if __name__ == "__main__":
    main()
