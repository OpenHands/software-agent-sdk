"""Tests for SOCKS proxy support (OpenHands/OpenHands-CLI#632).

When a user has SOCKS proxy env vars set (e.g. all_proxy=socks5://...),
httpx needs the socksio package to handle SOCKS proxy connections.
Without it, importing litellm (which creates an httpx.Client at module
level) crashes at startup with ImportError.
"""

import os
import subprocess
import sys


def test_socksio_is_installed():
    """Verify that socksio is installed as part of httpx[socks]."""
    import socksio  # noqa: F401


def test_httpx_socks_extra_available():
    """Verify httpx can create a client when SOCKS proxy env vars are set."""
    import httpx

    # Simulate a SOCKS proxy env var; the Client constructor should not raise
    # ImportError for socksio. We use a non-routable address so no real
    # connection is attempted.
    client = httpx.Client(proxy="socks5://127.0.0.1:19999")
    client.close()


def test_import_with_socks_proxy_env():
    """Ensure httpx can be imported and used when all_proxy is set to socks5."""
    env = os.environ.copy()
    env["all_proxy"] = "socks5://127.0.0.1:19999"
    env["https_proxy"] = "socks5://127.0.0.1:19999"

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import httpx; c = httpx.Client(); c.close(); print('ok')",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"Import failed with SOCKS proxy env vars set:\n{result.stderr}"
    )
    assert "ok" in result.stdout
