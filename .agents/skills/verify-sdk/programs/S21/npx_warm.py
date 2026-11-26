"""Start a conversation with a built-in provider's default `npx` command on a
machine where npm cannot install the provider, and report how the failure
reads to the program.

The provider is launched exactly as a launcher would:
`ACPAgent(acp_command=ACP_PROVIDERS[KEY].default_command)` in an ordinary
`Conversation`. npm is made to fail without touching anything outside the
run: the run's private HOME gets an `.npmrc` whose registry is a closed
loopback port (no retries), and the SDK keeps its ACP npm cache next to the
conversation store, which is inside the run and starts empty. Nothing is
downloaded.

Usage: npx_warm.py [PROVIDER]   (default: claude-code)
Needs `npx`; exit 3 (blocked) without it.
"""

import logging
import shutil
import sys
import time
from pathlib import Path

from sdk_verify import blocked, emit, run_paths

from openhands.sdk import Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.settings import ACP_PROVIDERS
from openhands.sdk.settings.acp_install_catalog import ACP_INSTALL_CATALOG


if shutil.which("npx") is None:
    blocked("npx (Node.js) is not on PATH")

REGISTRY = "http://127.0.0.1:9/"
key = sys.argv[1] if len(sys.argv) > 1 else "claude-code"
paths = run_paths()
home = Path.home()
if home.resolve() != paths["home"].resolve():
    blocked(f"HOME is not the run's private home: {home}")
(home / ".npmrc").write_text(
    f"registry={REGISTRY}\nfetch-retries=0\nupdate-notifier=false\n"
)
workspace = paths["workspace"]
workspace.mkdir(parents=True, exist_ok=True)
logged: list[str] = []


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            logged.append(record.getMessage())


logging.getLogger("openhands").addHandler(_Capture())

command = list(ACP_PROVIDERS[key].default_command)
packages = [p.pinned for p in ACP_INSTALL_CATALOG[key].packages]
conversation = Conversation(
    agent=ACPAgent(acp_command=command, acp_startup_timeout=60.0),
    workspace=str(workspace),
    persistence_dir=str(paths["conversations"]),
    visualizer=None,
)
conversation.send_message("hello")
raised = None
t0 = time.monotonic()
try:
    conversation.run()
except Exception as exc:  # recorded, the checks judge it
    raised = f"{type(exc).__name__}: {exc}"
seconds = round(time.monotonic() - t0, 1)
errors = [
    {"code": e.code, "detail": e.detail}
    for e in conversation.state.events
    if isinstance(e, ConversationErrorEvent)
]
status = conversation.state.execution_status.value
conversation.agent.close()
conversation.close()

# Where npm kept its cache and logs for this start, and what it downloaded.
caches = sorted(paths["run"].rglob("npm-cache"))
warm = [m for m in logged if "cache warm" in m]
emit(
    {
        "ok": True,
        "provider": key,
        "command": command,
        "packages": packages,
        "registry": REGISTRY,
        "raised": (raised or "")[:1500] or None,
        "raised_names_every_package": bool(raised)
        and all(p in raised for p in packages),
        "errors": [{"code": e["code"], "detail": e["detail"][:600]} for e in errors],
        "status": status,
        "seconds": seconds,
        "warm_warnings": [m[:1500] for m in warm],
        # Everything the program was told about the failure: the exception,
        # the error events and the SDK's warnings.
        "told": "\n".join([raised or "", *(e["detail"] for e in errors), *logged])[
            :6000
        ],
        "npm_cache_dirs": [str(c.relative_to(paths["run"])) for c in caches],
        "tarballs_in_cache": sum(
            1 for c in caches for _ in c.glob("_cacache/content-v2/**/*") if _.is_file()
        ),
        "home_npm_dir_created": (home / ".npm").exists(),
    }
)
