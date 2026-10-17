"""A small local website for the browser programs, served from a thread.

`serve()` writes the pages under the run's workspace and returns the base
URL; nothing touches the internet.
"""

from __future__ import annotations

import functools
import http.server
import os
import threading
import time
from pathlib import Path

from sdk_verify import run_paths


HOME = (
    """<!doctype html>
<html><head><title>Home Page</title></head>
<body>
<h1>Home Page</h1>
<p id="msg">nobody greeted yet</p>
<input id="name" placeholder="your name">
<button id="greet" onclick="document.getElementById('msg').textContent = """
    "'Hello ' + document.getElementById('name').value\">Greet</button>\n"
    """<a id="next" href="/page2.html">Go to page two</a>
<script>
localStorage.setItem('theme', 'dark');
document.cookie = 'session=abc123; path=/';
</script>
{filler}
<p id="bottom">END OF HOME PAGE</p>
</body></html>
"""
)

PAGE2 = """<!doctype html>
<html><head><title>Second Page</title></head>
<body><h1>Second Page</h1><p>Welcome to the second page.</p>
<a href="/index.html">Back home</a></body></html>
"""


def write_site() -> Path:
    root = run_paths()["run"] / "site"
    root.mkdir(exist_ok=True)
    filler = "\n".join(
        f"<p>Filler paragraph {i} for scrolling.</p>" for i in range(200)
    )
    (root / "index.html").write_text(HOME.replace("{filler}", filler))
    (root / "page2.html").write_text(PAGE2)
    # A page whose script never yields: every action on it hangs.
    (root / "hang.html").write_text(
        "<html><body><p>hang</p><script>while (true) {}</script></body></html>"
    )
    return root


class Handler(http.server.SimpleHTTPRequestHandler):
    """Static files, plus /slow, which answers only after a minute."""

    def do_GET(self) -> None:
        if self.path.startswith("/slow"):
            time.sleep(60)
        super().do_GET()

    def log_message(self, *args: object) -> None:
        pass


def serve() -> str:
    root = write_site()
    handler = functools.partial(Handler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def install_playwright_chromium() -> str | None:
    """Make the Playwright Chromium in PLAYWRIGHT_BROWSERS_PATH visible where
    `playwright install chromium` puts it for this HOME (~/.cache/ms-playwright).
    The SDK's detector looks only there, on PATH and in system paths."""
    source = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not source or not Path(source).is_dir():
        return None
    cache = Path.home() / ".cache" / "ms-playwright"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        cache.symlink_to(source)
    return str(cache)
