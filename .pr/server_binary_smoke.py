"""Start the packaged Agent Server, check readiness, and stop the owned process."""

import socket
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


BINARY = Path("dist/openhands-agent-server").resolve()


def main():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    server = subprocess.Popen(
        [str(BINARY), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError(
                    f"Packaged server exited with code {server.returncode}"
                )
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    if response.status == 200:
                        print("PASS: packaged server /health returned 200")
                        return
            except (HTTPError, URLError, TimeoutError):
                time.sleep(0.25)
        raise TimeoutError("Packaged server did not become healthy within 25 seconds")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    main()
