"""How a program's logs come out, depending on the LOG_* environment it
starts with. Each case is a fresh Python process (the SDK configures logging
once, at import) that logs through `get_logger`, a LiteLLM logger and an
httpx logger, and uses `rolling_log_view`; this program reports what each
wrote to its console and log file.
"""

import json
import os
import subprocess
import sys

from sdk_verify import emit, run_paths


CHILD = r"""
import logging, json, sys
from openhands.sdk import get_logger
from openhands.sdk.logger import rolling_log_view
log = get_logger("verify.app")
log.info("hello-info")
log.debug("hello-debug")
logging.getLogger("LiteLLM").info("litellm-info-line")
logging.getLogger("httpx").info("httpx-info-line")
with rolling_log_view(log, max_lines=2, header="HEAD") as view:
    for i in range(4):
        log.info(f"roll-{i}")
root = logging.getLogger()
print(json.dumps({
    "propagate": log.propagate,
    "root_level": logging.getLevelName(root.level),
    "handlers": [type(h).__name__ for h in root.handlers],
    "litellm_level": logging.getLevelName(logging.getLogger("LiteLLM").level),
    "openai_level": logging.getLevelName(logging.getLogger("openai").level),
    "httpx_level": logging.getLevelName(logging.getLogger("httpx").level),
    "libtmux_level": logging.getLevelName(logging.getLogger("libtmux").level),
    "rolling_buffer": list(view._buf),
}))
"""

# Variables a caller's shell may already carry; each case starts clean.
CLEAN = ("LOG_", "DEBUG", "CI", "GITHUB_ACTIONS")


def run_case(name: str, env: dict[str, str]) -> dict:
    base = {k: v for k, v in os.environ.items() if not k.startswith(CLEAN)}
    log_dir = run_paths()["run"] / "logs" / name
    proc = subprocess.run(
        [sys.executable, "-c", CHILD],
        env={
            **base,
            **{k: v.replace("{LOG_DIR}", str(log_dir)) for k, v in env.items()},
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = proc.stdout.strip().splitlines()
    facts = json.loads(lines[-1]) if lines and lines[-1].startswith("{") else {}
    console = proc.stderr + "\n".join(lines[:-1])
    json_lines = []
    for line in console.splitlines():
        try:
            json_lines.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    app_log = log_dir / "app.log"
    file_text = app_log.read_text() if app_log.exists() else None
    return {
        "exit": proc.returncode,
        **facts,
        "console_has_info": "hello-info" in console,
        "console_has_debug": "hello-debug" in console,
        "console_json_records": len(json_lines),
        "console_json_messages": [r.get("message", "")[:40] for r in json_lines][:8],
        "litellm_line_shown": "litellm-info-line" in console,
        "httpx_line_shown": "httpx-info-line" in console,
        "rolling_snapshot_records": sum(
            1 for r in json_lines if "HEAD" in str(r.get("message", ""))
        ),
        "file_exists": app_log.exists(),
        "file_has_info": bool(file_text) and "hello-info" in file_text,
        "file_is_json": bool(file_text)
        and all(line.startswith("{") for line in file_text.strip().splitlines()),
        "stderr_tail": proc.stderr[-300:] if proc.returncode else "",
    }


emit(
    {
        "ok": True,
        "default": run_case("default", {"LOG_LEVEL": "INFO"}),
        "json_file": run_case(
            "json_file",
            {
                "LOG_LEVEL": "INFO",
                "LOG_JSON": "1",
                "LOG_TO_FILE": "1",
                "LOG_DIR": "{LOG_DIR}",
                "LOG_ROTATE_WHEN": "H",
                "LOG_BACKUP_COUNT": "2",
            },
        ),
        "plain_file": run_case(
            "plain_file",
            {"LOG_LEVEL": "INFO", "LOG_TO_FILE": "true", "LOG_DIR": "{LOG_DIR}"},
        ),
        "ci": run_case("ci", {"LOG_LEVEL": "INFO", "CI": "true"}),
        "debug": run_case("debug", {"DEBUG": "true", "LOG_JSON": "1"}),
        "warning_level": run_case(
            "warning_level", {"LOG_LEVEL": "WARNING", "LOG_JSON": "1"}
        ),
        "no_auto_config": run_case(
            "no_auto_config", {"LOG_AUTO_CONFIG": "false", "LOG_JSON": "1"}
        ),
    }
)
