"""control-agent-server: the lever behind the verify-agent-server skill.

Launches an isolated OpenHands Agent Server from a checkout, keeps per-run
state on disk, and turns every agent-facing API step (REST, WebSocket, SDK
example) into a rerunnable command that prints exactly one JSON object.

Exit codes: 0 ok, 1 action failed, 2 usage, 3 environment.
Run `control-agent-server --help`, then `control-agent-server <command> --help`.
"""

# Help epilogs hold copy-pasteable example commands; keep each on one line.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import io
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlencode

import httpx
import psutil
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REPO_ROOT = SKILL_DIR.parent.parent.parent
FEATURE_MAP_DIR = SKILL_DIR / "references" / "feature-map"
CLI = "control-agent-server"
RUN_ENV = "AGENT_SERVER_VERIFY_RUN"
HOME_ENV = "AGENT_SERVER_VERIFY_HOME"
SESSION_HEADER = "X-Session-API-Key"
FAMILY_FILE_RE = re.compile(r"^F(\d{2})-[a-z0-9-]+\.md$")
SUB_FEATURE_RE = re.compile(r"^- `(F\d{2}\.[a-z0-9][a-z0-9-]*)`: \S")
RESULTS = ("pass", "fail", "xfail", "xpass", "blocked", "not-run")
FERNET_TOKEN = re.compile(r"gAAAAA[A-Za-z0-9_\-]{40,}={0,2}")

# Environment forwarded from the caller into a launched server. Everything
# else (LLM keys, OH_* overrides, cloud tokens) stays out unless passed with
# --env or --pass-env, so a run never inherits a developer's configuration.
FORWARDED_ENV = re.compile(
    r"^(PATH|LANG|LC_[A-Z]+|TZ|TERM|SHELL|USER|LOGNAME|TMPDIR"
    r"|(HTTP|HTTPS|NO|ALL)_PROXY|(http|https|no|all)_proxy"
    r"|SSL_CERT_FILE|SSL_CERT_DIR|REQUESTS_CA_BUNDLE|CURL_CA_BUNDLE"
    r"|NODE_EXTRA_CA_CERTS|PLAYWRIGHT_BROWSERS_PATH|DOCKER_HOST|UV_[A-Z_]+)$"
)


# --------------------------------------------------------------------------
# Output, errors, redaction
# --------------------------------------------------------------------------


class CliError(Exception):
    """A failure reported as one JSON object with an actionable hint."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        hint: str | None = None,
        example: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.hint = hint
        self.example = example
        self.data = data or {}


def usage_error(message: str, example: str | None = None) -> NoReturn:
    raise CliError(message, exit_code=2, example=example)


def env_error(message: str, hint: str | None = None) -> NoReturn:
    raise CliError(message, exit_code=3, hint=hint)


class Redactor:
    """Replaces every known secret value with a stable label in all output."""

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def add(self, value: str | None, label: str) -> None:
        if value and len(value) >= 6:
            self._secrets[value] = label

    def text(self, value: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret in value:
                value = value.replace(secret, f"<redacted:{self._secrets[secret]}>")
        # Cipher-encrypted secrets (X-Expose-Secrets: encrypted) are Fernet tokens.
        return FERNET_TOKEN.sub("<redacted:encrypted>", value)

    def obj(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(str(k)): self.obj(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.obj(v) for v in value]
        return value


REDACT = Redactor()
for _name in ("DEEPSEEK_API_KEY", "LLM_API_KEY", "OPENHANDS_API_KEY"):
    REDACT.add(os.environ.get(_name), _name)


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(REDACT.obj(payload), indent=2, default=str) + "\n")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_pairs(items: Sequence[str] | None, what: str) -> list[tuple[str, str]]:
    """KEY=VALUE pairs in order, repeated keys kept (ids=A&ids=B)."""
    pairs = []
    for item in items or []:
        if "=" not in item:
            usage_error(f"{what} must look like KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        pairs.append((key, value))
    return pairs


def parse_kv(items: Sequence[str] | None, what: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            usage_error(f"{what} must look like KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        result[key] = value
    return result


def git(*args: str, cwd: Path = REPO_ROOT) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def runs_home() -> Path:
    default = Path(os.environ.get("TMPDIR", "/tmp")) / "agent-server-verify"
    return Path(os.environ.get(HOME_ENV, str(default)))


@dataclass
class Run:
    """One launched (or attached) agent server and everything it owns."""

    dir: Path
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.dir.name

    @property
    def url(self) -> str:
        return str(self.data["url"])

    @property
    def private(self) -> Path:
        return self.dir / "private"

    @property
    def evidence(self) -> Path:
        return self.dir / "evidence"

    @property
    def fixtures(self) -> Path:
        return self.dir / "fixtures"

    @property
    def mode(self) -> str:
        return str(self.data.get("mode", "launch"))

    @property
    def session_key(self) -> str | None:
        path = self.private / "session_api_key"
        return path.read_text().strip() if path.exists() else None

    @property
    def log_path(self) -> Path:
        return self.private / "server.log"

    def save(self) -> None:
        (self.dir / "run.json").write_text(json.dumps(self.data, indent=2) + "\n")

    def register_secrets(self) -> None:
        REDACT.add(self.session_key, "session-api-key")
        for name in ("secret_key", "previous_session_api_key", "previous_secret_key"):
            path = self.private / name
            if path.exists():
                REDACT.add(path.read_text().strip(), name.replace("_", "-"))

    def alive(self) -> bool:
        if self.mode == "attach":
            return self.data.get("stopped_at") is None
        pgid = self.data.get("pgid")
        if not pgid or self.data.get("stopped_at"):
            return False
        try:
            os.killpg(int(pgid), 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def processes(self) -> list[dict[str, Any]]:
        return list(self.data.setdefault("processes", []))


def load_run(path: Path) -> Run:
    run_json = path / "run.json"
    if not run_json.exists():
        env_error(
            f"{path} is not a run directory (no run.json).",
            hint=f"Start one with `{CLI} launch --new --print-run`.",
        )
    run = Run(path, json.loads(run_json.read_text()))
    run.register_secrets()
    return run


def all_runs() -> list[Run]:
    home = runs_home()
    if not home.exists():
        return []
    runs = []
    for child in sorted(home.iterdir()):
        if (child / "run.json").exists():
            runs.append(load_run(child))
    return runs


def resolve_run(explicit: str | None, *, require_alive: bool = True) -> Run:
    """--run DIR, else $AGENT_SERVER_VERIFY_RUN, else the only live run."""
    chosen = explicit or os.environ.get(RUN_ENV)
    if chosen:
        candidate = Path(chosen)
        if not candidate.is_absolute() and not candidate.exists():
            candidate = runs_home() / chosen
        run = load_run(candidate)
    else:
        live = [r for r in all_runs() if r.alive()]
        if not live:
            env_error(
                "No live run.",
                hint=f"export {RUN_ENV}=$({CLI} launch --new --print-run)",
            )
        if len(live) > 1:
            env_error(
                f"{len(live)} live runs; refusing to guess which one to drive.",
                hint=f"export {RUN_ENV}=<run dir> (see `{CLI} runs`).",
            )
        run = live[0]
    if require_alive and not run.alive():
        env_error(
            f"Run {run.id} is not running.",
            hint=f"`{CLI} restart --run {run.dir}` or launch a new run.",
        )
    return run


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def http_client(timeout: float = 60.0) -> Any:
    return httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False)


def auth_headers(run: Run, mode: str = "header") -> dict[str, str]:
    key = run.session_key
    if mode == "none" or not key:
        return {}
    if mode == "bad":
        return {SESSION_HEADER: "qa-wrong-session-key"}
    if mode == "init":
        return {"X-Init-API-Key": (run.private / "secret_key").read_text().strip()}
    if mode == "bearer":
        return {"Authorization": f"Bearer {key}"}
    if mode == "bearer-bad":
        return {"Authorization": "Bearer qa-wrong-session-key"}
    if mode == "previous":
        previous = run.private / "previous_session_api_key"
        if not previous.exists():
            usage_error("--auth previous needs a key rotated by `restart --rotate-key`")
        return {SESSION_HEADER: previous.read_text().strip()}
    return {SESSION_HEADER: key}


def decode_body(response: Any, limit: int) -> tuple[Any, bool]:
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            return response.json(), False
        except ValueError:
            pass
    if content_type.startswith(("text/", "application/x-ndjson")) or not content_type:
        text = response.text
        return (text[:limit], len(text) > limit)
    return (f"<{len(response.content)} bytes of {content_type}>", False)


def status_matches(status: int, expect: str | None) -> bool:
    if not expect:
        return status < 400
    for token in expect.split(","):
        token = token.strip()
        if token.endswith("xx") and str(status)[0] == token[0]:
            return True
        if token.isdigit() and int(token) == status:
            return True
    return False


def dig(value: Any, path: str) -> Any:
    """Follow a dotted path (`items.0.id`) through dicts and lists."""
    current = value
    for part in [p for p in path.split(".") if p]:
        if isinstance(current, list) and part.lstrip("-").isdigit():
            index = int(part)
            if -len(current) <= index < len(current):
                current = current[index]
                continue
            return None
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return None
    return current


def save_evidence(run: Run, target: str, payload: Any, suffix: str = ".json") -> Path:
    """Save under evidence/<feature>/<name>; never overwrite (name-2, name-3)."""
    if "/" not in target:
        usage_error(
            "--save needs FEATURE/NAME", example="--save F03.create/start-response"
        )
    feature, name = target.split("/", 1)
    folder = run.evidence / feature
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}{suffix}"
    counter = 2
    while path.exists():
        path = folder / f"{name}-{counter}{suffix}"
        counter += 1
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    elif isinstance(payload, str):
        path.write_text(REDACT.text(payload))
    else:
        path.write_text(json.dumps(REDACT.obj(payload), indent=2, default=str) + "\n")
    return path


def api_call(
    run: Run,
    method: str,
    path: str,
    *,
    body: Any = None,
    params: list[tuple[str, str]] | dict[str, str] | None = None,
    auth: str = "header",
    headers: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes]] | None = None,
    form: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Any:
    request_headers = auth_headers(run, auth)
    request_headers.update(headers or {})
    with http_client(timeout) as client:
        kwargs: dict[str, Any] = {"headers": request_headers}
        if params:
            kwargs["params"] = params
        if files or form:
            kwargs["files"] = files
            kwargs["data"] = form
        elif body is not None:
            kwargs["json"] = body
        return client.request(method.upper(), run.url + path, **kwargs)


def api_json(run: Run, method: str, path: str, **kwargs: Any) -> Any:
    """Call the API and return JSON, raising CliError on a non-2xx status."""
    response = api_call(run, method, path, **kwargs)
    body, _ = decode_body(response, 2000)
    if response.status_code >= 400:
        raise CliError(
            f"{method} {path} returned {response.status_code}",
            data={"status": response.status_code, "body": body},
        )
    return body


# --------------------------------------------------------------------------
# launch / attach / stop / restart / runs / logs
# --------------------------------------------------------------------------


def checkout_python(checkout: Path) -> list[str]:
    venv_python = checkout / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python)]
    if shutil.which("uv"):
        return ["uv", "run", "--quiet", "--project", str(checkout), "python"]
    env_error(
        f"No Python environment for checkout {checkout}.",
        hint=f"Run `uv sync --dev` in {checkout}.",
    )


def tmux_dir(run: Run) -> str:
    """A short per-run tmux dir: sockets live at <dir>/tmux-<uid>/<name>, and a
    Unix socket path must stay under 108 bytes, which <run>/tmux overflows."""
    path = run.data.get("tmux_dir")
    if not path:
        path = f"/tmp/ohv-{secrets.token_hex(3)}"
        run.data["tmux_dir"] = path
        run.save()
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def server_env(
    run: Run, extra: dict[str, str], pass_env: Sequence[str]
) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if FORWARDED_ENV.match(k)}
    for name in pass_env:
        if name not in os.environ:
            usage_error(f"--pass-env {name}: not set in this shell")
        env[name] = os.environ[name]
    home = run.dir / "home"
    real_home = Path.home()
    env.update(
        {
            "HOME": str(home),
            "OH_PERSISTENCE_DIR": str(home / ".openhands"),
            "OPENHANDS_AGENT_SERVER_CONFIG_PATH": str(run.private / "config.json"),
            "TMUX_TMPDIR": tmux_dir(run),
            "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
            "UV_CACHE_DIR": env.get("UV_CACHE_DIR", str(real_home / ".cache" / "uv")),
            "NPM_CONFIG_CACHE": str(real_home / ".npm"),
            "OPENHANDS_BUILD_GIT_SHA": str(run.data.get("checkout_sha", "unknown")),
            "OPENHANDS_BUILD_GIT_REF": str(run.data.get("checkout_ref", "unknown")),
            "PYTHONUNBUFFERED": "1",
        }
    )
    for name in ("NO_PROXY", "no_proxy"):
        hosts = [h for h in env.get(name, "").split(",") if h]
        env[name] = ",".join(dict.fromkeys([*hosts, "127.0.0.1", "localhost"]))
    key = run.session_key
    if key:
        env["OH_SESSION_API_KEYS_0"] = key
    secret_file = run.private / "secret_key"
    if secret_file.exists():
        env["OH_SECRET_KEY"] = secret_file.read_text().strip()
    env.update(extra)
    return env


def write_server_config(run: Run, overrides: dict[str, Any]) -> None:
    server = run.dir / "server"
    config: dict[str, Any] = {
        "conversations_path": str(server / "workspace" / "conversations"),
        "workspace_path": str(server / "workspace" / "project"),
        "bash_events_dir": str(server / "workspace" / "bash_events"),
        "conversation_worktree_root": str(run.dir / "worktrees"),
        "vscode_port": int(run.data["vscode_port"]),
        "enable_vscode": bool(run.data.get("vscode", False)),
        "preload_tools": bool(run.data.get("preload_tools", False)),
        "deferred_init": bool(run.data.get("deferred_init", False)),
    }
    config.update(overrides)
    (run.private / "config.json").write_text(json.dumps(config, indent=2) + "\n")


def start_server(run: Run, timeout: float) -> dict[str, Any]:
    checkout = Path(run.data["checkout"])
    command = [
        *checkout_python(checkout),
        "-m",
        "openhands.agent_server",
        "--host",
        str(run.data["host"]),
        "--port",
        str(run.data["port"]),
    ]
    env = server_env(run, run.data.get("extra_env", {}), run.data.get("pass_env", []))
    log = run.log_path.open("ab")
    log.write(f"\n=== {now_iso()} start: {' '.join(command)}\n".encode())
    log.flush()
    process = subprocess.Popen(
        command,
        cwd=run.dir / "server",
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    run.data.update(
        {
            "pid": process.pid,
            "pgid": os.getpgid(process.pid),
            "started_at": now_iso(),
            "stopped_at": None,
            "command": command,
        }
    )
    run.save()
    started = time.monotonic()
    ready_path = "/alive" if run.data.get("deferred_init") else "/ready"
    last_error = ""
    with http_client(2.0) as client:
        while time.monotonic() - started < timeout:
            if process.poll() is not None:
                raise CliError(
                    f"Server exited with code {process.returncode} during startup.",
                    exit_code=3,
                    hint=f"Read the log: `{CLI} logs --tail 80`.",
                    data={"log": str(run.log_path)},
                )
            try:
                response = client.get(run.url + ready_path)
                if response.status_code == 200:
                    return {
                        "ready_ms": int((time.monotonic() - started) * 1000),
                        "ready_path": ready_path,
                    }
                last_error = f"{ready_path} -> {response.status_code}"
            except Exception as exc:
                last_error = type(exc).__name__
            time.sleep(0.25)
    raise CliError(
        f"Server not ready after {timeout:.0f}s ({last_error}).",
        exit_code=3,
        hint=f"`{CLI} logs --tail 80`, then `{CLI} stop`.",
    )


def new_run_dir(label: str | None) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    name = f"{stamp}-{suffix}" + (f"-{label}" if label else "")
    path = runs_home() / name
    for sub in (
        "private",
        "evidence",
        "fixtures",
        "server",
        "home",
        "tmux",
        "worktrees",
    ):
        (path / sub).mkdir(parents=True, exist_ok=True)
    os.chmod(path / "private", 0o700)
    return path


def write_git_identity(home: Path) -> None:
    (home / ".gitconfig").write_text(
        "[user]\n\tname = QA Verify\n\temail = qa-verify@example.invalid\n"
        "[init]\n\tdefaultBranch = main\n[safe]\n\tdirectory = *\n"
    )


def cmd_launch(args: argparse.Namespace) -> dict[str, Any]:
    if not args.new:
        live = [r for r in all_runs() if r.alive()]
        chosen = os.environ.get(RUN_ENV)
        if chosen:
            live = [r for r in live if str(r.dir) == chosen or r.id == chosen]
        if len(live) == 1:
            run = live[0]
            if args.print_run:
                print(run.dir)
                raise SystemExit(0)
            return {
                "ok": True,
                "already_running": True,
                "run": str(run.dir),
                "url": run.url,
            }
    checkout = Path(args.checkout).resolve() if args.checkout else REPO_ROOT
    if not (checkout / "openhands-agent-server").is_dir():
        usage_error(f"{checkout} is not an agent-sdk checkout")
    available = psutil.virtual_memory().available
    if available < 1_000_000_000 and not args.force:
        env_error(
            f"Only {available // 1_000_000} MB of memory free; a run needs ~1 GB.",
            hint=f"Stop runs you are done with (`{CLI} runs`), or pass --force.",
        )
    run = Run(new_run_dir(args.name))
    port = args.port or free_port(args.host)
    run.data = {
        "id": run.id,
        "mode": "launch",
        "host": args.host,
        "port": port,
        "url": f"http://{args.host}:{port}",
        "vscode_port": free_port(args.host),
        "vscode": args.vscode,
        "preload_tools": args.preload_tools,
        "deferred_init": args.deferred_init,
        "auth": not args.no_auth,
        "checkout": str(checkout),
        "checkout_sha": git("rev-parse", "HEAD", cwd=checkout),
        "checkout_ref": git("rev-parse", "--abbrev-ref", "HEAD", cwd=checkout),
        "extra_env": parse_kv(args.env, "--env"),
        "pass_env": list(args.pass_env or []),
        "created_at": now_iso(),
        "processes": [],
    }
    if not args.no_auth:
        (run.private / "session_api_key").write_text(secrets.token_urlsafe(24))
    if not args.no_secret_key:
        (run.private / "secret_key").write_text(secrets.token_urlsafe(32))
    run.register_secrets()
    write_git_identity(run.dir / "home")
    overrides = json.loads(args.config_json) if args.config_json else {}
    if args.share_conversations_from:
        other = resolve_run(args.share_conversations_from, require_alive=False)
        overrides["conversations_path"] = str(
            other.dir / "server" / "workspace" / "conversations"
        )
    if args.canvas_ingress:
        overrides["app_backend_public_url"] = run.url
    if args.webhook_sink:
        sink = start_http_sink(run, "webhooks")
        overrides.setdefault("webhooks", []).append(
            {"base_url": sink["url"], "flush_delay": args.webhook_flush_delay}
        )
    if args.webhook_url:
        overrides.setdefault("webhooks", []).append(
            {"base_url": args.webhook_url, "flush_delay": args.webhook_flush_delay}
        )
    run.data["config_overrides"] = overrides
    write_server_config(run, overrides)
    run.save()
    readiness = start_server(run, args.timeout)
    if args.print_run:
        print(run.dir)
        raise SystemExit(0)
    return {
        "ok": True,
        "run": str(run.dir),
        "url": run.url,
        "port": port,
        "pgid": run.data["pgid"],
        "checkout": str(checkout),
        "checkout_sha": run.data["checkout_sha"],
        "auth": run.data["auth"],
        **readiness,
        "next": [
            f"export {RUN_ENV}={run.dir}",
            f"{CLI} doctor",
        ],
    }


def cmd_attach(args: argparse.Namespace) -> dict[str, Any]:
    run = Run(new_run_dir(args.name or "attach"))
    url = args.url.rstrip("/")
    run.data = {
        "id": run.id,
        "mode": "attach",
        "url": url,
        "host": url.split("://", 1)[-1].split(":")[0],
        "auth": bool(args.key_env),
        "created_at": now_iso(),
        "processes": [],
    }
    if args.key_env:
        if args.key_env not in os.environ:
            usage_error(f"--key-env {args.key_env} is not set in this shell")
        (run.private / "session_api_key").write_text(os.environ[args.key_env])
    run.register_secrets()
    run.save()
    if args.print_run:
        print(run.dir)
        raise SystemExit(0)
    return {"ok": True, "run": str(run.dir), "url": url, "mode": "attach"}


def stop_process_group(pgid: int, grace: float = 15.0) -> str:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return "already-exited"
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return "terminated"
        time.sleep(0.2)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)
    return "killed"


def stop_helpers(run: Run) -> list[dict[str, Any]]:
    stopped = []
    for proc in run.processes():
        if proc.get("stopped_at"):
            continue
        outcome = stop_process_group(int(proc["pgid"]), grace=5.0)
        proc["stopped_at"] = now_iso()
        stopped.append({"name": proc.get("name"), "outcome": outcome})
    return stopped


def reap_orphans(run: Run) -> list[dict[str, Any]]:
    """Kill leftover processes whose HOME is inside this run.

    The server and everything it spawns (tmux, MCP servers, Canvas App
    backends) run with HOME under the run's private home directory, which no
    other run or agent shell shares; a SIGKILLed server leaves them behind.
    """
    home = str(run.dir / "home")
    own = {os.getpid(), *(p.pid for p in psutil.Process().parents())}
    reaped = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        if proc.pid in own:
            continue
        with contextlib.suppress(psutil.Error):
            if proc.environ().get("HOME", "").startswith(home):
                proc.kill()
                reaped.append(
                    {
                        "pid": proc.pid,
                        "cmdline": " ".join(proc.info["cmdline"] or [])[:160],
                    }
                )
    return reaped


def cmd_stop(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    result: dict[str, Any] = {"ok": True, "run": str(run.dir)}
    result["helpers"] = stop_helpers(run)
    if run.mode == "launch" and run.data.get("pgid") and not run.data.get("stopped_at"):
        result["server"] = stop_process_group(int(run.data["pgid"]))
        port = int(run.data["port"])
        deadline = time.monotonic() + 5
        while port_open(str(run.data["host"]), port) and time.monotonic() < deadline:
            time.sleep(0.2)
        result["port_closed"] = not port_open(str(run.data["host"]), port)
        if not result["port_closed"]:
            result["ok"] = False
    result["orphans_reaped"] = reap_orphans(run)
    run.data["stopped_at"] = now_iso()
    run.save()
    if run.data.get("tmux_dir"):
        shutil.rmtree(run.data["tmux_dir"], ignore_errors=True)
    if args.purge_private:
        for sub in ("private", "home", "server", "tmux", "worktrees", "fixtures"):
            shutil.rmtree(run.dir / sub, ignore_errors=True)
        result["purged"] = True
    result["evidence"] = str(run.evidence)
    result["evidence_files"] = sum(1 for p in run.evidence.rglob("*") if p.is_file())
    return result


def cmd_restart(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    if run.mode != "launch":
        usage_error("restart only works on launched runs")
    if run.alive() and args.hard:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(int(run.data["pgid"]), signal.SIGKILL)
        time.sleep(0.5)
    elif run.alive():
        stop_process_group(int(run.data["pgid"]))
    if args.rotate_key and run.data.get("auth"):
        old = run.session_key or ""
        (run.private / "previous_session_api_key").write_text(old)
        (run.private / "session_api_key").write_text(secrets.token_urlsafe(24))
        run.register_secrets()
    if args.restore_secret_key:
        previous = run.private / "previous_secret_key"
        if not previous.exists():
            usage_error("--restore-secret-key needs an earlier --rotate-secret-key")
        current = (run.private / "secret_key").read_text()
        (run.private / "secret_key").write_text(previous.read_text())
        previous.write_text(current)
    if args.rotate_secret_key:
        old_secret = (run.private / "secret_key").read_text()
        (run.private / "previous_secret_key").write_text(old_secret)
        (run.private / "secret_key").write_text(secrets.token_urlsafe(32))
        run.register_secrets()
    if args.reset_config:
        run.data["config_overrides"] = {}
        run.data["extra_env"] = {}
    if args.env:
        run.data.setdefault("extra_env", {}).update(parse_kv(args.env, "--env"))
    if args.config_json:
        run.data.setdefault("config_overrides", {}).update(json.loads(args.config_json))
    write_server_config(run, run.data.get("config_overrides", {}))
    port = int(run.data["port"])
    deadline = time.monotonic() + 10
    while port_open(str(run.data["host"]), port) and time.monotonic() < deadline:
        time.sleep(0.2)
    readiness = start_server(run, args.timeout)
    return {
        "ok": True,
        "run": str(run.dir),
        "url": run.url,
        "rotated_key": bool(args.rotate_key),
        "hard": bool(args.hard),
        "rotated_secret_key": bool(args.rotate_secret_key),
        **readiness,
    }


def cmd_runs(args: argparse.Namespace) -> dict[str, Any]:
    del args
    rows = []
    for run in all_runs():
        rows.append(
            {
                "run": str(run.dir),
                "mode": run.mode,
                "url": run.data.get("url"),
                "alive": run.alive(),
                "created_at": run.data.get("created_at"),
                "checkout_sha": run.data.get("checkout_sha"),
            }
        )
    return {"ok": True, "home": str(runs_home()), "runs": rows}


def cmd_logs(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    if not run.log_path.exists():
        return {"ok": True, "lines": [], "note": "no server log (attached run?)"}
    lines = run.log_path.read_text(errors="replace").splitlines()
    if args.grep:
        pattern = re.compile(args.grep)
        lines = [line for line in lines if pattern.search(line)]
    ok = args.expect_min is None or len(lines) >= args.expect_min
    if args.expect_none:
        ok = not lines
    return {
        "ok": ok,
        "log": str(run.log_path),
        "lines": [REDACT.text(line) for line in lines[-args.tail :]],
        "total_matching": len(lines),
    }


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def listening_pids(port: int) -> set[int]:
    pids: set[int] = set()
    with contextlib.suppress(Exception):
        for conn in psutil.net_connections(kind="tcp"):
            local = conn.laddr
            if conn.status == "LISTEN" and local and local[1] == port and conn.pid:
                pids.add(conn.pid)
    return pids


def ws_url(run: Run, path: str) -> str:
    return (
        run.url.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + path
    )


def ws_probe(run: Run, path: str, auth: str) -> dict[str, Any]:
    """Open a socket, authenticate, and report whether it stayed open."""
    result: dict[str, Any] = {"path": path, "auth": auth}
    try:
        with connect(ws_url(run, path), open_timeout=5, close_timeout=2) as ws:
            if auth != "none":
                key = run.session_key if auth == "first-frame" else "qa-wrong-key"
                ws.send(json.dumps({"type": "auth", "session_api_key": key}))
            try:
                frame = ws.recv(timeout=1.5)
                result["first_frame"] = str(frame)[:200]
                result["open"] = True
            except TimeoutError:
                result["open"] = True
    except ConnectionClosed as exc:
        result["open"] = False
        result["close_code"] = exc.rcvd.code if exc.rcvd else None
    except Exception as exc:
        result["open"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any, required: bool = True) -> None:
        checks.append({"name": name, "ok": ok, "required": required, "detail": detail})

    if run.mode == "launch":
        check("process-group", run.alive(), {"pgid": run.data.get("pgid")})
        owners = listening_pids(int(run.data["port"]))
        pgid = int(run.data.get("pgid") or 0)
        owned = any(_pgid(pid) == pgid for pid in owners)
        check(
            "port-owner",
            owned or not owners,
            {"port": run.data["port"], "listening_pids": sorted(owners)},
        )
    with http_client(10) as client:
        for path in ("/alive", "/health", "/ready"):
            try:
                response = client.get(run.url + path)
                expected = 503 if (path == "/ready" and _dormant(run)) else 200
                check(
                    path,
                    response.status_code in (200, expected),
                    {"status": response.status_code},
                    required=path != "/ready" or not run.data.get("deferred_init"),
                )
            except Exception as exc:
                check(path, False, f"{type(exc).__name__}: {exc}")
        try:
            info = client.get(run.url + "/server_info").json()
            expected_sha = run.data.get("checkout_sha")
            sha_ok = run.mode == "attach" or info.get("build_git_sha") == expected_sha
            check(
                "server-info",
                sha_ok,
                {
                    "version": info.get("version"),
                    "sdk_version": info.get("sdk_version"),
                    "build_git_sha": info.get("build_git_sha"),
                    "expected_sha": expected_sha,
                    "usable_tools": info.get("usable_tools"),
                },
            )
        except Exception as exc:
            check("server-info", False, f"{type(exc).__name__}: {exc}")
        probe = "/api/conversations/count"
        if run.data.get("auth"):
            denied = client.get(run.url + probe)
            check(
                "auth-rejects-missing-key",
                denied.status_code == 401,
                denied.status_code,
            )
        allowed = client.get(run.url + probe, headers=auth_headers(run))
        dormant = _dormant(run)
        check(
            "auth-accepts-key",
            allowed.status_code == 200 or (dormant and allowed.status_code == 503),
            {"status": allowed.status_code, "dormant": dormant},
        )
        if (run.private / "previous_session_api_key").exists():
            stale = client.get(run.url + probe, headers=auth_headers(run, "previous"))
            check(
                "auth-rejects-rotated-key", stale.status_code == 401, stale.status_code
            )
        try:
            spec = client.get(run.url + "/openapi.json").json()
            operations = sum(len(v) for v in spec.get("paths", {}).values())
            check("openapi", operations > 0, {"operations": operations})
        except Exception as exc:
            check("openapi", False, f"{type(exc).__name__}: {exc}")
    if run.data.get("auth"):
        check(
            "ws-rejects-bad-key",
            not ws_probe(run, "/sockets/bash-events", "bad")["open"],
            ws_probe(run, "/sockets/bash-events", "bad"),
        )
    check(
        "ws-accepts-first-frame-auth",
        ws_probe(
            run,
            "/sockets/bash-events",
            "first-frame" if run.data.get("auth") else "none",
        )["open"],
        None,
    )
    if run.log_path.exists():
        text = run.log_path.read_text(errors="replace")
        tracebacks = text.count("Traceback (most recent call last)")
        check("log-tracebacks", tracebacks == 0, {"count": tracebacks}, required=False)
    ok = all(c["ok"] for c in checks if c["required"])
    result = {"ok": ok, "run": str(run.dir), "url": run.url, "checks": checks}
    if not ok:
        result["hint"] = (
            f"Fix the failing check before driving; `{CLI} logs --tail 80` shows "
            "the server log."
        )
    return result


def _pgid(pid: int) -> int:
    try:
        return os.getpgid(pid)
    except ProcessLookupError:
        return -1


def _dormant(run: Run) -> bool:
    if not run.data.get("deferred_init"):
        return False
    with contextlib.suppress(Exception), http_client(5) as client:
        body = client.get(run.url + "/api/init", headers=auth_headers(run)).json()
        return str(body.get("status", "")).lower() != "ready"
    return True


# --------------------------------------------------------------------------
# api
# --------------------------------------------------------------------------


class CookieJar:
    """A small browser-like jar: Path and Max-Age/Expires honored, Secure
    cookies sent to loopback (as browsers treat localhost), stored per run."""

    def __init__(self, run: Run, name: str) -> None:
        self.path = run.fixtures / "jars" / f"{name}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cookies: dict[str, dict[str, Any]] = (
            json.loads(self.path.read_text()) if self.path.exists() else {}
        )

    def header_for(self, request_path: str) -> str:
        now = time.time()
        pairs = [
            f"{name}={c['value']}"
            for name, c in self.cookies.items()
            if request_path.split("?")[0].startswith(c["path"])
            and (c["expires"] is None or c["expires"] > now)
        ]
        return "; ".join(pairs)

    def store(self, set_cookies: list[str], request_path: str) -> None:
        for header in set_cookies:
            parts = [p.strip() for p in header.split(";")]
            name, _, value = parts[0].partition("=")
            attrs = {k.lower(): v for k, _, v in (p.partition("=") for p in parts[1:])}
            expires: float | None = None
            if "max-age" in attrs:
                expires = time.time() + int(attrs["max-age"] or 0)
            path = attrs.get("path") or request_path.rsplit("/", 1)[0] or "/"
            if expires is not None and expires <= time.time():
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = {
                    "value": value,
                    "path": path,
                    "expires": expires,
                    "attributes": sorted(attrs),
                }
        self.path.write_text(json.dumps(self.cookies, indent=2))

    def summary(self) -> dict[str, Any]:
        return {
            name: {"path": c["path"], "attributes": c["attributes"]}
            for name, c in self.cookies.items()
        }


def read_body(args: argparse.Namespace) -> Any:
    sources = [s for s in (args.json, args.json_file, args.stdin) if s]
    if len(sources) > 1:
        usage_error("Use only one of --json, --json-file, --stdin")
    if args.json:
        return json.loads(args.json)
    if args.json_file:
        return json.loads(Path(args.json_file).read_text())
    if args.stdin:
        return json.loads(sys.stdin.read())
    return None


CHECK_OPS = (
    "eq",
    "ne",
    "lt",
    "le",
    "gt",
    "ge",
    "contains",
    "not-contains",
    "matches",
    "exists",
    "missing",
    "len-eq",
    "len-ge",
)


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def evaluate_check(body: Any, spec: Sequence[str]) -> dict[str, Any]:
    """One `--check FIELD OP [VALUE]` assertion against a JSON body."""
    if len(spec) < 2 or spec[1] not in CHECK_OPS:
        usage_error(
            f"--check needs FIELD OP [VALUE] with OP in {', '.join(CHECK_OPS)}",
            example="--check execution_status eq finished",
        )
    path, op = spec[0], spec[1]
    expected = spec[2] if len(spec) > 2 else ""
    actual = body if path in (".", "") else dig(body, path)
    present = actual is not None or path in (".", "")
    ok: bool
    if op == "exists":
        ok = present
    elif op == "missing":
        ok = not present
    elif op in ("eq", "ne"):
        same = _as_text(actual) == expected or (
            _as_number(actual) is not None
            and _as_number(actual) == _as_number(expected)
        )
        ok = same if op == "eq" else not same
    elif op in ("lt", "le", "gt", "ge"):
        a, b = _as_number(actual), _as_number(expected)
        ok = (
            a is not None
            and b is not None
            and {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]
        )
    elif op in ("contains", "not-contains"):
        if isinstance(actual, list):
            found = any(_as_text(item) == expected for item in actual) or any(
                expected in _as_text(item) for item in actual
            )
        else:
            found = actual is not None and expected in _as_text(actual)
        ok = found if op == "contains" else not found
    elif op == "matches":
        ok = actual is not None and re.search(expected, _as_text(actual)) is not None
    else:
        size = len(actual) if isinstance(actual, list | dict | str) else None
        target = _as_number(expected)
        ok = (
            size is not None
            and target is not None
            and (size == target if op == "len-eq" else size >= target)
        )
    shown = _as_text(actual)
    return {
        "check": " ".join(spec),
        "ok": ok,
        "actual": shown if len(shown) <= 200 else shown[:200] + "…",
    }


def cmd_api(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    if not args.path.startswith("/"):
        usage_error(
            "PATH must start with /", example=f"{CLI} api GET /api/conversations/count"
        )
    body = read_body(args)
    files: dict[str, tuple[str, bytes]] = {}
    for item in args.file or []:
        if "=" not in item:
            usage_error(
                "--file must look like FIELD=PATH", example="--file file=./notes.txt"
            )
        name, path = item.split("=", 1)
        files[name] = (Path(path).name, Path(path).read_bytes())
    form = parse_kv(args.form, "--form")
    headers = {}
    for item in args.header or []:
        if ":" not in item:
            usage_error("--header must look like 'Name: value'")
        key, value = item.split(":", 1)
        headers[key.strip()] = value.strip()
    jar = CookieJar(run, args.jar) if args.jar else None
    if jar:
        jar_cookie = jar.header_for(args.path)
        if jar_cookie:
            headers["Cookie"] = "; ".join(
                filter(None, [headers.get("Cookie"), jar_cookie])
            )
    if args.cookie:
        headers["Cookie"] = "; ".join(
            filter(None, [headers.get("Cookie"), args.cookie])
        )
    started = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        response = api_call(
            run,
            args.method,
            args.path,
            body=body,
            params=parse_pairs(args.query, "--query"),
            auth=args.auth,
            headers=headers,
            files=files or None,
            form=form or None,
            timeout=args.timeout,
        )
        decoded, truncated = decode_body(response, args.max_chars)
        if args.sse:
            decoded = {"frames": parse_sse(response.text)}
        settled = status_matches(response.status_code, args.expect) and all(
            evaluate_check(decoded, spec)["ok"] for spec in args.check or []
        )
        if settled or time.monotonic() - started >= args.until_ok:
            break
        time.sleep(0.5)
    elapsed = int((time.monotonic() - started) * 1000)
    exchange = {
        "request": {
            "method": args.method.upper(),
            "path": args.path,
            "query": parse_pairs(args.query, "--query"),
            "auth": args.auth,
            "body": body,
            "files": sorted(files),
        },
        "response": {
            "status": response.status_code,
            "headers": {
                k: v
                for k, v in response.headers.items()
                if k.lower().startswith("x-openhands")
                or k.lower()
                in (
                    "content-type",
                    "content-disposition",
                    "location",
                    "set-cookie",
                    "content-length",
                    "cache-control",
                    "etag",
                    "access-control-allow-origin",
                    "access-control-allow-credentials",
                    "access-control-expose-headers",
                )
            },
            "body": decoded,
            "truncated": truncated,
        },
        "elapsed_ms": elapsed,
    }
    ok = status_matches(response.status_code, args.expect)
    checks = [evaluate_check(decoded, spec) for spec in args.check or []]
    lowered = {k.lower(): v for k, v in response.headers.items()}
    for spec in args.check_header or []:
        name = spec[0].lower()
        verdict = evaluate_check({name: lowered.get(name)}, [name, *spec[1:]])
        checks.append({**verdict, "check": "header " + verdict["check"]})
    if any(not c["ok"] for c in checks):
        ok = False
    if args.all_headers:
        exchange["response"]["headers"] = dict(response.headers.items())
    result: dict[str, Any] = {"ok": ok, "status": response.status_code, **exchange}
    if checks:
        result["checks"] = checks
    if args.until_ok:
        result["attempts"] = attempts
    slow = args.expect_max_ms is not None and elapsed > args.expect_max_ms
    if slow:
        result["ok"] = ok = False
    if args.quiet:
        result["request"] = {k: v for k, v in result["request"].items() if k != "body"}
        result["response"] = {
            k: v for k, v in result["response"].items() if k != "body"
        }
    if jar:
        jar.store(response.headers.get_list("set-cookie"), args.path)
        result["jar"] = jar.summary()
    if args.raw_out:
        Path(args.raw_out).write_bytes(response.content)
        result["raw_out"] = args.raw_out
    if args.save:
        result["saved"] = str(save_evidence(run, args.save, exchange))
    if not ok:
        failed = [c["check"] for c in checks if not c["ok"]]
        if not status_matches(response.status_code, args.expect):
            result["hint"] = (
                f"expected {args.expect or '<400'}, got {response.status_code}"
            )
        elif failed:
            result["hint"] = "failed check: " + "; ".join(failed)
        elif slow:
            result["hint"] = f"took {elapsed} ms, limit {args.expect_max_ms} ms"
    if args.print_header is not None:
        header = response.headers.get(args.print_header)
        if header is None or not ok:
            emit(result)
            raise SystemExit(1)
        print(header)
        raise SystemExit(0)
    if args.field is not None:
        value = dig(decoded, args.field)
        if value is None or not ok:
            emit(result)
            raise SystemExit(1)
        print(value if isinstance(value, str) else json.dumps(value))
        raise SystemExit(0)
    return result


# --------------------------------------------------------------------------
# ws
# --------------------------------------------------------------------------


def parse_sse(text: str) -> list[Any]:
    """`data:` payloads of a text/event-stream body (JSON-decoded when possible)."""
    frames: list[Any] = []
    for block in text.split("\n\n"):
        data = "\n".join(
            line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
        )
        if not data:
            continue
        try:
            frames.append(json.loads(data))
        except ValueError:
            frames.append(data)
    return frames


def maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def parse_headers(items: Sequence[str] | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in items or []:
        if ":" not in item:
            usage_error("--header must look like 'Name: value'")
        name, value = item.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


WS_AUTH_MODES = [
    "first-frame",
    "query",
    "header",
    "none",
    "bad",
    "bad-query",
    "bad-header",
    "previous",
    "key",
]
SESSION_FRAME_TYPES = {
    "sync",
    "durable",
    "transient",
    "item_started",
    "delta",
    "item_aborted",
    "error",
}


def frame_kind(frame: Any) -> str:
    """Event kind for /sockets/events frames; envelope type for session frames
    (`durable:MessageEvent` for durable and transient event envelopes)."""
    if not isinstance(frame, dict):
        return type(frame).__name__
    if frame.get("type") in SESSION_FRAME_TYPES:
        event = frame.get("event")
        if isinstance(event, dict) and event.get("kind"):
            return f"{frame['type']}:{event['kind']}"
        return str(frame["type"])
    return str(frame.get("kind") or frame.get("type") or "?")


def frame_matches(frame: Any, conditions: dict[str, str]) -> bool:
    return all(str(dig(frame, key)) == value for key, value in conditions.items())


def ws_session(
    run: Run,
    path: str,
    *,
    auth: str,
    query: dict[str, str],
    sends: Sequence[str],
    duration: float,
    until_kind: str | None,
    until: dict[str, str],
    count: int | None,
    sink: Callable[[dict[str, Any]], None],
    extra_headers: dict[str, str] | None = None,
    explicit_key: str | None = None,
) -> dict[str, Any]:
    params = dict(query)
    headers: dict[str, str] = dict(extra_headers or {})
    key = run.session_key
    if auth == "previous":
        key = auth_headers(run, "previous")[SESSION_HEADER]
        auth = "first-frame"
    if auth == "key":
        key = explicit_key or ""
        auth = "first-frame"
    if auth == "query" and key:
        params["session_api_key"] = key
    if auth == "header" and key:
        headers[SESSION_HEADER] = key
    if auth == "bad-query":
        params["session_api_key"] = "qa-wrong-key"
    if auth == "bad-header":
        headers[SESSION_HEADER] = "qa-wrong-key"
    url = ws_url(run, path) + (f"?{urlencode(params)}" if params else "")
    frames = 0
    kinds: dict[str, int] = {}
    close: dict[str, Any] | None = None
    reason = "duration"
    deadline = time.monotonic() + duration
    try:
        with connect(
            url,
            open_timeout=10,
            close_timeout=2,
            additional_headers=headers,
            max_size=None,
        ) as ws:
            if auth == "first-frame" and (key or explicit_key is not None):
                ws.send(json.dumps({"type": "auth", "session_api_key": key}))
            elif auth == "bad":
                ws.send(json.dumps({"type": "auth", "session_api_key": "qa-wrong-key"}))
            for message in sends:
                ws.send(message)
                sink(
                    {"direction": "sent", "at": now_iso(), "frame": maybe_json(message)}
                )
            while time.monotonic() < deadline:
                try:
                    raw = ws.recv(timeout=max(0.05, deadline - time.monotonic()))
                except TimeoutError:
                    break
                try:
                    frame: Any = json.loads(raw)
                except (TypeError, ValueError):
                    frame = str(raw)
                frames += 1
                kind = frame_kind(frame)
                kinds[kind] = kinds.get(kind, 0) + 1
                sink(
                    {
                        "direction": "received",
                        "at": now_iso(),
                        "kind": kind,
                        "frame": frame,
                    }
                )
                if until_kind and kind == until_kind and frame_matches(frame, until):
                    reason = f"until-kind {until_kind}"
                    break
                if until and not until_kind and frame_matches(frame, until):
                    reason = "until"
                    break
                if count and frames >= count:
                    reason = f"count {count}"
                    break
    except ConnectionClosed as exc:
        close = {
            "code": exc.rcvd.code if exc.rcvd else None,
            "reason": exc.rcvd.reason if exc.rcvd else None,
        }
        reason = "closed"
    except Exception as exc:
        close = {"error": f"{type(exc).__name__}: {exc}"}
        if isinstance(exc, InvalidStatus):
            close["http_status"] = exc.response.status_code
        reason = "error"
    return {"frames": frames, "kinds": kinds, "stopped": reason, "close": close}


def cmd_ws_listen(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    captured: list[dict[str, Any]] = []
    out_path: Path | None = Path(args.out) if args.out else None
    out_file = out_path.open("a") if out_path else None

    def sink(entry: dict[str, Any]) -> None:
        captured.append(entry)
        if out_file:
            out_file.write(json.dumps(REDACT.obj(entry), default=str) + "\n")
            out_file.flush()

    def stop_on_term(signum: int, frame: Any) -> None:
        del signum, frame
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_term)
    try:
        summary = ws_session(
            run,
            args.path,
            auth=args.auth,
            query=parse_kv(args.query, "--query"),
            sends=args.send or [],
            duration=args.duration,
            until_kind=args.until_kind,
            until=parse_kv(args.until, "--until"),
            count=args.count,
            sink=sink,
            extra_headers=parse_headers(args.header),
            explicit_key=args.key,
        )
        sink(
            {
                "direction": "closed" if summary["stopped"] == "closed" else "ended",
                "at": now_iso(),
                "stopped": summary["stopped"],
                "close": summary["close"],
            }
        )
    except KeyboardInterrupt:
        sink({"direction": "stopped", "at": now_iso()})
        if out_file:
            out_file.close()
        raise SystemExit(0) from None
    if out_file:
        out_file.close()
    result: dict[str, Any] = {"ok": True, "path": args.path, **summary}
    if args.until_kind and not summary["stopped"].startswith("until"):
        result["ok"] = False
        result["hint"] = f"no {args.until_kind} frame within {args.duration}s"
    if args.expect_close is not None:
        code = (summary.get("close") or {}).get("code")
        result["ok"] = result["ok"] and code == args.expect_close
    if args.expect_reject is not None:
        rejected = (summary.get("close") or {}).get("http_status")
        result["ok"] = result["ok"] and rejected == args.expect_reject
    if args.expect_open:
        result["ok"] = (
            result["ok"]
            and summary["stopped"] != "closed"
            and summary["stopped"] != "error"
        )
    received = [e for e in captured if e["direction"] == "received"]
    for kind in args.expect_kind or []:
        if not any(e.get("kind") == kind for e in received):
            result["ok"] = False
            result.setdefault("missing_kinds", []).append(kind)
    for kind in args.expect_no_kind or []:
        if any(e.get("kind") == kind for e in received):
            result["ok"] = False
            result.setdefault("unexpected_kinds", []).append(kind)
    if args.expect_frames is not None and len(received) != args.expect_frames:
        result["ok"] = False
        result["hint"] = f"expected {args.expect_frames} frames, got {len(received)}"
    if args.save:
        result["saved"] = str(
            save_evidence(run, args.save, {"summary": summary, "frames": captured})
        )
    result["first_frames"] = [
        {"kind": e.get("kind"), "frame": _brief(e.get("frame"))}
        for e in captured
        if e["direction"] == "received"
    ][: args.show]
    return result


def _brief(frame: Any) -> Any:
    text = json.dumps(frame, default=str)
    return frame if len(text) <= 400 else text[:400] + "…"


def cmd_ws_start(args: argparse.Namespace) -> dict[str, Any]:
    """Record a socket in a background process until `ws stop` or --duration."""
    run = resolve_run(args.run)
    name = args.name or f"ws-{secrets.token_hex(3)}"
    out = run.dir / "captures" / f"{name}.jsonl"
    out.parent.mkdir(exist_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--run",
        str(run.dir),
        "ws",
        "listen",
        args.path,
        "--auth",
        args.auth,
        "--duration",
        str(args.duration),
        "--out",
        str(out),
        "--show",
        "0",
    ]
    for item in args.query or []:
        command += ["--query", item]
    for message in args.send or []:
        command += ["--send", message]
    for header in args.header or []:
        command += ["--header", header]
    if args.key is not None:
        command += ["--key", args.key]
    log = (run.dir / "captures" / f"{name}.log").open("ab")
    process = subprocess.Popen(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, RUN_ENV: str(run.dir)},
    )
    entry = {
        "name": name,
        "kind": "ws-capture",
        "pid": process.pid,
        "pgid": os.getpgid(process.pid),
        "out": str(out),
        "path": args.path,
        "started_at": now_iso(),
    }
    run.data.setdefault("processes", []).append(entry)
    run.save()
    time.sleep(args.settle)
    return {
        "ok": process.poll() is None,
        "capture": name,
        "out": str(out),
        "hint": f"`{CLI} ws stop {name}` (or `ws read {name}`) after triggering the action",
    }


def _capture_entry(run: Run, name: str) -> dict[str, Any]:
    for proc in run.data.get("processes", []):
        if proc.get("name") == name and proc.get("kind") == "ws-capture":
            return proc
    usage_error(
        f"No capture named {name}",
        example=f"{CLI} ws start /sockets/events/<id> --name events",
    )


def _read_capture(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize_capture(
    entries: list[dict[str, Any]],
    kinds_filter: str | None,
    contains: str | None,
    show: int,
) -> dict[str, Any]:
    received = [e for e in entries if e.get("direction") == "received"]
    closed = [e for e in entries if e.get("direction") == "closed"]
    kinds: dict[str, int] = {}
    for e in received:
        kinds[str(e.get("kind"))] = kinds.get(str(e.get("kind")), 0) + 1
    selected = received
    if kinds_filter:
        wanted = set(kinds_filter.split(","))
        selected = [e for e in selected if e.get("kind") in wanted]
    if contains:
        selected = [
            e for e in selected if contains in json.dumps(e.get("frame"), default=str)
        ]
    return {
        "frames": len(received),
        "kinds": kinds,
        "close": closed[-1].get("close") if closed else None,
        "matching": len(selected),
        "matching_kinds": sorted({str(e.get("kind")) for e in selected}),
        "frames_shown": [
            {"kind": e.get("kind"), "frame": _brief(e.get("frame"))}
            for e in selected[:show]
        ],
    }


def capture_verdict(args: argparse.Namespace, summary: dict[str, Any]) -> bool:
    ok = True
    filtered = bool(args.kinds or args.contains)
    if args.expect_kind:
        pool = summary["matching_kinds"] if filtered else summary["kinds"]
        ok = args.expect_kind in pool
    if args.expect_none:
        ok = ok and summary["matching"] == 0
    elif args.expect_min is not None:
        ok = ok and summary["matching"] >= args.expect_min
    elif filtered and args.expect_kind is None:
        ok = ok and summary["matching"] > 0
    close = summary.get("close")
    if args.expect_close is not None:
        ok = ok and (close or {}).get("code") == args.expect_close
    if args.expect_open:
        ok = ok and close is None
    return ok


def cmd_ws_read(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    entry = _capture_entry(run, args.name)
    deadline = time.monotonic() + args.wait
    while True:
        entries = _read_capture(Path(entry["out"]))
        summary = summarize_capture(entries, args.kinds, args.contains, args.show)
        if capture_verdict(args, summary) or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    result: dict[str, Any] = {
        "ok": capture_verdict(args, summary),
        "capture": args.name,
        "out": entry["out"],
        **summary,
    }
    if args.save:
        result["saved"] = str(
            save_evidence(run, args.save, {"capture": args.name, "frames": entries})
        )
    return result


def cmd_ws_stop(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    entry = _capture_entry(run, args.name)
    deadline = time.monotonic() + args.wait
    while args.wait and time.monotonic() < deadline:
        summary = summarize_capture(
            _read_capture(Path(entry["out"])), args.kinds, args.contains, 0
        )
        if capture_verdict(args, summary):
            break
        time.sleep(0.25)
    args.wait = 0
    if not entry.get("stopped_at"):
        stop_process_group(int(entry["pgid"]), grace=3)
        entry["stopped_at"] = now_iso()
        run.save()
    return cmd_ws_read(args)


# --------------------------------------------------------------------------
# llm
# --------------------------------------------------------------------------

PRESETS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "env": "DEEPSEEK_API_KEY",
        "profiles": [
            {
                "name": "deepseek-flash",
                "model": "deepseek/deepseek-flash",
                "activate": True,
            },
            {
                "name": "deepseek-pro",
                "model": "deepseek/deepseek-v4-pro",
                "activate": False,
            },
        ],
    }
}


def read_api_key(args: argparse.Namespace, default_env: str) -> str:
    if args.api_key_file:
        key = Path(args.api_key_file).read_text().strip()
    else:
        env_name = args.api_key_env or default_env
        key = os.environ.get(env_name, "")
        if not key:
            env_error(
                f"${env_name} is empty.",
                hint="Export the key, or pass --api-key-file PATH. Never put keys in argv.",
            )
    REDACT.add(key, "llm-api-key")
    return key


def save_llm_profile_extra(args: argparse.Namespace) -> dict[str, Any]:
    extra: dict[str, Any] = json.loads(args.extra_json) if args.extra_json else {}
    if args.num_retries is not None:
        extra["num_retries"] = args.num_retries
    if args.llm_timeout is not None:
        extra["timeout"] = args.llm_timeout
    return extra


def save_llm_profile(
    run: Run,
    name: str,
    model: str,
    key: str,
    base_url: str | None,
    activate: bool,
    validate: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    llm: dict[str, Any] = {"model": model, "api_key": key, **(extra or {})}
    if base_url:
        llm["base_url"] = base_url
    row: dict[str, Any] = {"profile": name, "model": model}
    if validate:
        # Pre-flight: a one-token completion with the draft, before saving.
        checked = api_call(
            run,
            "POST",
            f"/api/profiles/{name}/validate",
            body={"llm": llm},
            timeout=120,
        )
        verdict = decode_body(checked, 500)[0]
        row["validate"] = {"status": checked.status_code, "body": verdict}
        if checked.status_code >= 400 or not dig(verdict, "valid"):
            row["error"] = "validation failed; profile not saved"
            return row
    saved = api_call(run, "POST", f"/api/profiles/{name}", body={"llm": llm})
    row["saved"] = saved.status_code
    if saved.status_code >= 400:
        row["error"] = decode_body(saved, 500)[0]
        return row
    if activate:
        activated = api_call(run, "POST", f"/api/profiles/{name}/activate")
        row["activated"] = activated.status_code
    return row


def cmd_llm_preset(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    preset = PRESETS.get(args.preset)
    if not preset:
        usage_error(f"Unknown preset {args.preset!r}; known: {', '.join(PRESETS)}")
    key = read_api_key(args, str(preset["env"]))
    rows = [
        save_llm_profile(
            run,
            p["name"],
            p["model"],
            key,
            None,
            p["activate"],
            not args.no_validate and p["activate"],
        )
        for p in preset["profiles"]
    ]
    ok = all("error" not in r and r.get("activated", 200) < 400 for r in rows)
    return {"ok": ok, "profiles": rows}


def cmd_llm_set(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    key = "qa-stub-key" if args.no_api_key else read_api_key(args, "LLM_API_KEY")
    row = save_llm_profile(
        run,
        args.profile,
        args.model,
        key,
        args.base_url,
        not args.no_activate,
        not args.no_validate,
        extra=save_llm_profile_extra(args),
    )
    ok = "error" not in row and row.get("activated", 200) < 400
    return {"ok": ok, "profile": row}


def cmd_llm_show(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    profiles = api_json(run, "GET", "/api/profiles")
    return {"ok": True, "profiles": profiles}


# --------------------------------------------------------------------------
# conversation
# --------------------------------------------------------------------------


def conversation_status(run: Run, conversation_id: str) -> str:
    info = api_json(run, "GET", f"/api/conversations/{conversation_id}")
    return str(info.get("execution_status", "unknown")).lower()


def wait_for_status(
    run: Run,
    conversation_id: str,
    wanted: set[str],
    timeout: float,
    min_wait: float = 0.0,
) -> dict[str, Any]:
    started = time.monotonic()
    seen: list[str] = []
    status = "unknown"
    while time.monotonic() - started < timeout:
        status = conversation_status(run, conversation_id)
        if not seen or seen[-1] != status:
            seen.append(status)
        if status in wanted and time.monotonic() - started >= min_wait:
            return {
                "ok": True,
                "status": status,
                "transitions": seen,
                "waited_ms": int((time.monotonic() - started) * 1000),
            }
        time.sleep(1.0)
    return {
        "ok": False,
        "status": status,
        "transitions": seen,
        "hint": f"still {status} after {timeout:.0f}s; wanted {sorted(wanted)}",
    }


def build_start_body(run: Run, args: argparse.Namespace) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(args.body_json) if args.body_json else {}
    if (
        "agent" not in body
        and "agent_settings" not in body
        and "agent_profile_id" not in body
    ):
        if args.agent_profile:
            body["agent_profile_id"] = resolve_agent_profile_id(run, args.agent_profile)
        elif args.placeholder_agent:
            body["agent_settings"] = {
                "agent_kind": "openhands",
                "llm": {
                    "model": "openai/qa-placeholder",
                    "api_key": "qa-placeholder",
                    "base_url": "http://127.0.0.1:9/v1",
                    "num_retries": 0,
                },
                "tools": [],
            }
        else:
            settings = api_json(
                run, "GET", "/api/settings", headers={"X-Expose-Secrets": "encrypted"}
            )
            agent_settings = (
                settings.get("agent_settings") if isinstance(settings, dict) else None
            )
            if not agent_settings:
                raise CliError(
                    "GET /api/settings has no agent_settings to start from.",
                    hint=f"Run `{CLI} llm preset deepseek` first.",
                )
            body["agent_settings"] = agent_settings
            body["secrets_encrypted"] = True
    if args.workspace:
        working_dir = Path(args.workspace).resolve()
    elif "workspace" not in body:
        # The request requires a workspace; give each conversation its own.
        working_dir = (
            run.fixtures / "workspaces" / datetime.now(UTC).strftime("%H%M%S-%f")
        )
        working_dir.mkdir(parents=True)
    if "workspace" not in body or args.workspace:
        body["workspace"] = {"kind": "LocalWorkspace", "working_dir": str(working_dir)}
    if args.prompt and not args.no_run:
        # The server always runs initial_message; --no-run sends the prompt
        # afterwards with POST /events run=false instead.
        body["initial_message"] = {
            "role": "user",
            "content": [{"type": "text", "text": args.prompt}],
            "run": True,
        }
    for key, value in parse_kv(args.tag, "--tag").items():
        body.setdefault("tags", {})[key] = value
    if args.max_iterations:
        body["max_iterations"] = args.max_iterations
    if args.llm_json and isinstance(body.get("agent_settings"), dict):
        body["agent_settings"].setdefault("llm", {}).update(json.loads(args.llm_json))
    if args.tools is not None and isinstance(body.get("agent_settings"), dict):
        names = [t for t in args.tools.split(",") if t and t != "none"]
        body["agent_settings"]["tools"] = [{"name": name} for name in names]
    if args.no_autotitle:
        body["autotitle"] = False
    for name, value in parse_kv(args.secret, "--secret").items():
        REDACT.add(value, f"secret:{name}")
        body.setdefault("secrets", {})[name] = {"kind": "StaticSecret", "value": value}
    if args.confirmation_policy:
        body["confirmation_policy"] = {
            "kind": CONFIRMATION_POLICIES[args.confirmation_policy]
        }
    return body


CONFIRMATION_POLICIES = {
    "always": "AlwaysConfirm",
    "never": "NeverConfirm",
    "risky": "ConfirmRisky",
}


def resolve_agent_profile_id(run: Run, name_or_id: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F-]{32,36}", name_or_id):
        return name_or_id
    listing = api_json(run, "GET", "/api/agent-profiles")
    items = listing.get("profiles", listing) if isinstance(listing, dict) else listing
    for item in items or []:
        if isinstance(item, dict) and item.get("name") == name_or_id:
            return str(item.get("id"))
    usage_error(f"No agent profile named {name_or_id!r}")


def cmd_conversation_start(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    body = build_start_body(run, args)
    response = api_call(
        run, "POST", "/api/conversations", body=body, timeout=args.timeout
    )
    decoded, _ = decode_body(response, 4000)
    if response.status_code >= 400:
        raise CliError(
            f"POST /api/conversations returned {response.status_code}",
            data={"status": response.status_code, "body": decoded},
        )
    conversation_id = str(dig(decoded, "id"))
    if args.prompt and args.no_run:
        api_json(
            run,
            "POST",
            f"/api/conversations/{conversation_id}/events",
            body={
                "role": "user",
                "content": [{"type": "text", "text": args.prompt}],
                "run": False,
            },
        )
    if args.title:
        api_json(
            run,
            "PATCH",
            f"/api/conversations/{conversation_id}",
            body={"title": args.title},
        )
    result: dict[str, Any] = {
        "ok": True,
        "id": conversation_id,
        "status": response.status_code,
        "execution_status": dig(decoded, "execution_status"),
        "workspace": dig(decoded, "workspace.working_dir"),
    }
    if args.save:
        result["saved"] = str(
            save_evidence(run, args.save, {"request": body, "response": decoded})
        )
    if args.wait:
        waited = wait_for_status(
            run, conversation_id, set(args.until.split(",")), args.timeout, min_wait=1.0
        )
        result["wait"] = waited
        result["ok"] = waited["ok"]
    if args.print_id:
        if not result["ok"]:
            emit(result)
            raise SystemExit(1)
        print(conversation_id)
        raise SystemExit(0)
    return result


def cmd_conversation_wait(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    waited = wait_for_status(run, args.id, set(args.until.split(",")), args.timeout)
    return {"id": args.id, **waited}


def cmd_conversation_send(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    body = {
        "role": "user",
        "content": [{"type": "text", "text": args.text}],
        "run": not args.no_run,
    }
    response = api_call(run, "POST", f"/api/conversations/{args.id}/events", body=body)
    result: dict[str, Any] = {
        "ok": response.status_code < 400,
        "status": response.status_code,
        "body": decode_body(response, 2000)[0],
    }
    if args.wait and result["ok"]:
        waited = wait_for_status(
            run, args.id, set(args.until.split(",")), args.timeout, min_wait=1.0
        )
        result["wait"] = waited
        result["ok"] = waited["ok"]
    return result


def iter_events(
    run: Run, conversation_id: str, page_limit: int = 100
) -> Iterator[dict[str, Any]]:
    page_id: str | None = None
    while True:
        params = {"limit": str(page_limit)}
        if page_id:
            params["page_id"] = page_id
        page = api_json(
            run,
            "GET",
            f"/api/conversations/{conversation_id}/events/search",
            params=params,
        )
        yield from page.get("items", [])
        page_id = page.get("next_page_id")
        if not page_id:
            return


def event_text(event: dict[str, Any]) -> str:
    return json.dumps(event, default=str)


def cmd_conversation_events(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    events = list(iter_events(run, args.id))
    kinds: dict[str, int] = {}
    for event in events:
        kinds[str(event.get("kind"))] = kinds.get(str(event.get("kind")), 0) + 1
    selected = events
    if args.kinds:
        wanted = set(args.kinds.split(","))
        selected = [e for e in selected if e.get("kind") in wanted]
    if args.contains:
        selected = [e for e in selected if args.contains in event_text(e)]
    if args.key:
        selected = [e for e in selected if e.get("key") == args.key]
    result: dict[str, Any] = {
        "ok": True,
        "id": args.id,
        "total": len(events),
        "kinds": kinds,
        "matching": len(selected),
        "events": [_event_brief(e, args.full) for e in selected[-args.show :]]
        if args.show
        else [],
    }
    if args.expect_kind:
        result["ok"] = any(e.get("kind") == args.expect_kind for e in selected)
    elif args.contains and args.expect_count is None and args.expect_min is None:
        result["ok"] = bool(selected)
    if args.expect_count is not None:
        result["ok"] = result["ok"] and len(selected) == args.expect_count
    if args.expect_min is not None:
        result["ok"] = result["ok"] and len(selected) >= args.expect_min
    if args.save:
        result["saved"] = str(save_evidence(run, args.save, {"events": selected}))
    return result


def _event_brief(event: dict[str, Any], full: bool) -> Any:
    if full:
        return event
    brief: dict[str, Any] = {
        "id": event.get("id"),
        "kind": event.get("kind"),
        "source": event.get("source"),
        "timestamp": event.get("timestamp"),
    }
    for key in (
        "tool_name",
        "llm_message",
        "observation",
        "action",
        "error",
        "code",
        "detail",
        "key",
        "value",
    ):
        if key in event:
            value = event[key]
            text = value if isinstance(value, str) else json.dumps(value, default=str)
            brief[key] = text if len(text) < 300 else text[:300] + "…"
    return brief


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def run_cmd(
    command: Sequence[str], cwd: Path, env: dict[str, str] | None = None
) -> None:
    subprocess.run(
        list(command),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def fixture_git_repo(run: Run, name: str) -> dict[str, Any]:
    repo = run.fixtures / name
    if repo.exists():
        return {"path": str(repo), "already_exists": True}
    repo.mkdir(parents=True)
    env = {"GIT_CONFIG_GLOBAL": str(run.dir / "home" / ".gitconfig")}
    run_cmd(["git", "init", "-q", "-b", "main"], repo, env)
    (repo / "README.md").write_text(
        "# QA fixture repo\n\nCreated by control-agent-server.\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text(
        'def greet(name):\n    return f"hello {name}"\n'
    )
    run_cmd(["git", "add", "-A"], repo, env)
    run_cmd(["git", "commit", "-q", "-m", "Initial commit"], repo, env)
    (repo / "src" / "util.py").write_text("def add(a, b):\n    return a + b\n")
    run_cmd(["git", "add", "-A"], repo, env)
    run_cmd(["git", "commit", "-q", "-m", "Add util"], repo, env)
    (repo / "README.md").write_text("# QA fixture repo\n\nModified, not committed.\n")
    (repo / "notes.txt").write_text("untracked file\n")
    return {
        "path": str(repo),
        "commits": 2,
        "modified": ["README.md"],
        "untracked": ["notes.txt"],
    }


def fixture_skill(run: Run, name: str) -> dict[str, Any]:
    skill_dir = write_skill(run.fixtures / "skills" / name, name)
    return {"path": str(skill_dir), "name": name}


def write_skill(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: QA fixture skill {name}; mention QA_SKILL_MARKER "
        f"when asked about {name}.\ntriggers:\n- {name}\n---\n\n# {name}\n\n"
        "When this skill is active, include the exact phrase QA_SKILL_MARKER.\n"
    )
    return folder


def write_plugin(folder: Path, name: str) -> Path:
    (folder / ".plugin").mkdir(parents=True, exist_ok=True)
    (folder / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": f"QA fixture plugin {name}",
                "entry_command": "qa-hello",
            },
            indent=2,
        )
        + "\n"
    )
    (folder / "commands").mkdir(exist_ok=True)
    (folder / "commands" / "qa-hello.md").write_text(
        "---\ndescription: Say hello from the QA plugin\n---\n\nReply with QA_PLUGIN_HELLO.\n"
    )
    write_skill(folder / "skills" / f"{name}-skill", f"{name}-skill")
    (folder / "hooks").mkdir(exist_ok=True)
    (folder / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {"type": "command", "command": "echo QA_PLUGIN_HOOK"}
                            ],
                        }
                    ]
                }
            },
            indent=2,
        )
        + "\n"
    )
    return folder


def fixture_plugin(run: Run, name: str) -> dict[str, Any]:
    folder = write_plugin(run.fixtures / "plugins" / name, name)
    return {
        "path": str(folder),
        "name": name,
        "skill": f"{name}-skill",
        "command": f"/{name}:qa-hello",
    }


def git_commit_all(repo: Path, run: Run, message: str) -> str:
    env = {"GIT_CONFIG_GLOBAL": str(run.dir / "home" / ".gitconfig")}
    run_cmd(["git", "add", "-A"], repo, env)
    run_cmd(["git", "commit", "-q", "-m", message], repo, env)
    return git("rev-parse", "HEAD", cwd=repo)


def fixture_git_source(run: Run, name: str) -> dict[str, Any]:
    """A local git repository holding a skill and a plugin (install from git)."""
    repo = run.fixtures / "git" / name
    env = {"GIT_CONFIG_GLOBAL": str(run.dir / "home" / ".gitconfig")}
    if (repo / ".git").exists():
        marker = repo / "skills" / f"{name}-skill" / "SKILL.md"
        marker.write_text(marker.read_text() + f"\nRevision {now_iso()}.\n")
        head = git_commit_all(repo, run, "Bump the QA skill")
        bumped = True
    else:
        repo.mkdir(parents=True)
        run_cmd(["git", "init", "-q", "-b", "main"], repo, env)
        write_skill(repo / "skills" / f"{name}-skill", f"{name}-skill")
        write_plugin(repo / "plugins" / f"{name}-plugin", f"{name}-plugin")
        head = git_commit_all(repo, run, "QA skill and plugin")
        bumped = False
    return {
        "path": str(repo),
        "url": f"file://{repo}",
        "head": head,
        "bumped": bumped,
        "skill": f"{name}-skill",
        "skill_repo_path": f"skills/{name}-skill",
        "plugin": f"{name}-plugin",
        "plugin_repo_path": f"plugins/{name}-plugin",
    }


def fixture_marketplace(run: Run, name: str) -> dict[str, Any]:
    root = run.fixtures / "marketplaces" / name
    write_plugin(root / "plugins" / f"{name}-plugin", f"{name}-plugin")
    write_skill(root / "skills" / f"{name}-skill", f"{name}-skill")
    (root / ".plugin").mkdir(parents=True, exist_ok=True)
    (root / ".plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": name,
                "owner": {"name": "QA"},
                "plugins": [
                    {"name": f"{name}-plugin", "source": f"./plugins/{name}-plugin"}
                ],
                "skills": [
                    {"name": f"{name}-skill", "source": f"./skills/{name}-skill"}
                ],
            },
            indent=2,
        )
        + "\n"
    )
    return {
        "path": str(root),
        "name": name,
        "plugin": f"{name}-plugin",
        "skill": f"{name}-skill",
        "registration": {"name": name, "source": str(root), "auto_load": True},
    }


def fixture_project(run: Run, name: str) -> dict[str, Any]:
    """A git project with project skills, AGENTS.md, a sub-agent and hooks."""
    root = run.fixtures / "projects" / name
    env = {"GIT_CONFIG_GLOBAL": str(run.dir / "home" / ".gitconfig")}
    if not root.exists():
        root.mkdir(parents=True)
        run_cmd(["git", "init", "-q", "-b", "main"], root, env)
    write_skill(root / ".agents" / "skills" / f"{name}-skill", f"{name}-skill")
    (root / ".openhands" / "skills").mkdir(parents=True, exist_ok=True)
    (root / ".openhands" / "skills" / "qa-legacy.md").write_text(
        "---\nname: qa-legacy\ntriggers:\n- qa-legacy\n---\n\nLegacy QA microagent.\n"
    )
    (root / "AGENTS.md").write_text("# QA project\n\nAlways mention QA_AGENTS_MD.\n")
    (root / ".agents" / "agents").mkdir(parents=True, exist_ok=True)
    (root / ".agents" / "agents" / "qa-helper.md").write_text(
        "---\nname: qa-helper\ndescription: QA fixture sub-agent that answers briefly.\n---\n\n"
        "You are a QA helper. Reply with QA_SUBAGENT.\n"
    )
    (root / ".openhands" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {"type": "command", "command": "echo QA_PROJECT_HOOK"}
                            ],
                        }
                    ]
                }
            },
            indent=2,
        )
        + "\n"
    )
    return {
        "path": str(root),
        "name": name,
        "skill": f"{name}-skill",
        "legacy_skill": "qa-legacy",
        "sub_agent": "qa-helper",
    }


def fixture_mcp_server(run: Run, name: str) -> dict[str, Any]:
    folder = run.fixtures / "mcp"
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / f"{name}.py"
    script.write_text(
        "from mcp.server.fastmcp import FastMCP\n\n"
        f"mcp = FastMCP({name!r})\n\n\n"
        "@mcp.tool()\n"
        "def qa_echo(text: str) -> str:\n"
        '    """Echo text back with a QA marker."""\n'
        '    return f"QA_MCP_ECHO:{text}"\n\n\n'
        'if __name__ == "__main__":\n'
        "    mcp.run()\n"
    )
    python = checkout_python(Path(run.data.get("checkout", REPO_ROOT)))
    server = {"command": python[0], "args": [*python[1:], str(script)]}
    return {
        "path": str(script),
        "name": name,
        "mcp_server": server,
        "settings_json": {name: server},
    }


def serve_http_sink(port: int, out: Path) -> None:
    """Hidden helper process: record every request as one JSONL line."""

    class Handler(BaseHTTPRequestHandler):
        def _record(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body: Any = json.loads(raw) if raw else None
            except ValueError:
                body = raw.decode(errors="replace")
            entry = {
                "at": now_iso(),
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body,
            }
            with out.open("a") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")
            status = int(os.environ.get("QA_SINK_STATUS", "200"))
            reply = os.environ.get("QA_SINK_BODY", '{"ok": true}').encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        do_POST = do_PUT = do_PATCH = do_GET = do_DELETE = _record

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            del format, args

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def start_http_sink(
    run: Run, name: str, status: int = 200, body: str | None = None
) -> dict[str, Any]:
    for proc in run.processes():
        if proc.get("name") == name and not proc.get("stopped_at"):
            return {"path": proc["out"], "url": proc["url"], "already_running": True}
    port = free_port()
    out = run.fixtures / f"{name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.touch()
    log = (run.fixtures / f"{name}.log").open("ab")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "_http-sink",
            str(port),
            str(out),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={
            **os.environ,
            "QA_SINK_STATUS": str(status),
            "QA_SINK_BODY": body or '{"ok": true}',
        },
    )
    deadline = time.monotonic() + 10
    while not port_open("127.0.0.1", port) and time.monotonic() < deadline:
        time.sleep(0.1)
    url = f"http://127.0.0.1:{port}"
    run.data.setdefault("processes", []).append(
        {
            "name": name,
            "kind": "http-sink",
            "pid": process.pid,
            "pgid": os.getpgid(process.pid),
            "url": url,
            "out": str(out),
            "started_at": now_iso(),
        }
    )
    run.save()
    return {
        "path": str(out),
        "url": url,
        "config_json": {"webhooks": [{"base_url": url, "flush_delay": 1.0}]},
    }


def stub_completion(step: dict[str, Any], model: str) -> dict[str, Any]:
    """An OpenAI chat.completion for one scripted step."""
    usage = step.get("usage") or [10, 5]
    message: dict[str, Any] = {"role": "assistant", "content": step.get("reply")}
    finish = "stop"
    if "tool" in step:
        message["content"] = step.get("reply")
        message["tool_calls"] = [
            {
                "id": f"call_qa_{secrets.token_hex(4)}",
                "type": "function",
                "function": {
                    "name": step["tool"],
                    "arguments": json.dumps(step.get("args", {})),
                },
            }
        ]
        finish = "tool_calls"
    return {
        "id": f"chatcmpl-qa-{secrets.token_hex(4)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": usage[0],
            "completion_tokens": usage[1],
            "total_tokens": usage[0] + usage[1],
        },
    }


def stub_stream(completion: dict[str, Any]) -> bytes:
    """The same completion as server-sent events, for stream=true requests."""
    choice = completion["choices"][0]
    delta = dict(choice["message"])
    if "tool_calls" in delta:
        delta["tool_calls"] = [
            dict(c, index=i) for i, c in enumerate(delta["tool_calls"])
        ]
    chunks = [
        {
            **completion,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        },
        {
            **completion,
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}
            ],
            "usage": completion["usage"],
        },
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def serve_llm_stub(port: int, script: Path, out: Path) -> None:
    """Hidden helper process: an OpenAI-compatible provider that replays a script."""
    calls = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            body = {"object": "list", "data": [{"id": "qa-stub", "object": "model"}]}
            self._reply(200, json.dumps(body).encode(), "application/json")

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
            steps = json.loads(script.read_text()) if script.exists() else []
            index = calls["n"]
            calls["n"] += 1
            step = (
                steps[min(index, len(steps) - 1)] if steps else {"reply": "stub reply"}
            )
            with out.open("a") as handle:
                handle.write(
                    json.dumps(
                        {
                            "at": now_iso(),
                            "method": "POST",
                            "path": self.path,
                            "call": index,
                            "step": step,
                            "body": request,
                        }
                    )
                    + "\n"
                )
            if "hang" in step:
                time.sleep(float(step["hang"]))
            if "status" in step:
                error = step.get("body") or {
                    "error": {
                        "message": "qa stub error",
                        "type": "qa_stub",
                        "code": step["status"],
                    }
                }
                self._reply(
                    int(step["status"]), json.dumps(error).encode(), "application/json"
                )
                return
            completion = stub_completion(step, str(request.get("model", "qa-stub")))
            if request.get("stream"):
                self._reply(200, stub_stream(completion), "text/event-stream")
            else:
                self._reply(200, json.dumps(completion).encode(), "application/json")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            del format, args

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def parse_stub_step(text: str) -> dict[str, Any]:
    kind, _, rest = text.partition(":")
    if kind == "reply":
        text, _, usage = rest.partition("@usage=")
        step: dict[str, Any] = {"reply": text}
        if usage:
            prompt, _, completion = usage.partition(",")
            step["usage"] = [int(prompt), int(completion or 0)]
        return step
    if kind == "tool":
        name, _, args = rest.partition(":")
        return {"tool": name, "args": json.loads(args) if args else {}}
    if kind == "status":
        return {"status": int(rest)}
    if kind == "hang":
        return {"hang": float(rest)}
    usage_error(
        f"Unknown stub step {text!r}",
        example='--step reply:hello --step \'tool:finish:{"message":"done"}\' --step status:500 --step hang:60',
    )


def start_llm_stub(run: Run, name: str, steps: Sequence[str]) -> dict[str, Any]:
    folder = run.fixtures / "llm-stub"
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / f"{name}.json"
    script.write_text(json.dumps([parse_stub_step(s) for s in steps], indent=2))
    out = run.fixtures / f"{name}.jsonl"
    for proc in run.processes():
        if proc.get("name") == name and not proc.get("stopped_at"):
            return {
                "path": str(script),
                "url": proc["url"],
                "steps": len(steps),
                "already_running": True,
                "requests_log": str(out),
            }
    out.touch()
    port = free_port()
    log = (folder / f"{name}.log").open("ab")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "_llm-stub",
            str(port),
            str(script),
            str(out),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while not port_open("127.0.0.1", port) and time.monotonic() < deadline:
        time.sleep(0.1)
    url = f"http://127.0.0.1:{port}"
    run.data.setdefault("processes", []).append(
        {
            "name": name,
            "kind": "llm-stub",
            "pid": process.pid,
            "pgid": os.getpgid(process.pid),
            "url": url,
            "out": str(out),
            "started_at": now_iso(),
        }
    )
    run.save()
    return {
        "path": str(script),
        "url": url,
        "steps": len(steps),
        "requests_log": str(out),
        "profile": f"{CLI} llm set --profile {name} --model openai/qa-stub "
        f"--base-url {url}/v1 --no-api-key --no-validate",
    }


def fixture_http_sink(run: Run, name: str) -> dict[str, Any]:
    return start_http_sink(run, name)


def cmd_sink_read(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    out = run.fixtures / f"{args.name}.jsonl"
    if not out.exists():
        usage_error(
            f"No HTTP sink named {args.name}",
            example=f"{CLI} fixture http-sink --name {args.name}",
        )
    deadline = time.monotonic() + args.wait
    while True:
        entries = [
            json.loads(line) for line in out.read_text().splitlines() if line.strip()
        ]
        if args.path:
            entries = [e for e in entries if e["path"].startswith(args.path)]
        if args.contains:
            entries = [
                e for e in entries if args.contains in json.dumps(e, default=str)
            ]
        enough = args.expect_min is None or len(entries) >= args.expect_min
        if enough or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    paths: dict[str, int] = {}
    for entry in entries:
        paths[entry["path"]] = paths.get(entry["path"], 0) + 1
    result: dict[str, Any] = {
        "ok": True,
        "requests": len(entries),
        "paths": paths,
        "last": [_brief(e) for e in entries[-args.show :]] if args.show else [],
    }
    if args.expect_min is not None:
        result["ok"] = len(entries) >= args.expect_min
    if args.expect_max is not None:
        result["ok"] = result["ok"] and len(entries) <= args.expect_max
    if args.save:
        result["saved"] = str(save_evidence(run, args.save, {"requests": entries}))
    return result


def conversation_dir(conversation_id: str) -> str:
    return "server/workspace/conversations/" + conversation_id.replace("-", "").lower()


def run_relative(run: Run, path: str) -> Path:
    target = (run.dir / path).resolve()
    if not target.is_relative_to(run.dir.resolve()):
        usage_error(f"{path} is outside the run directory")
    return target


def cmd_state_ls(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    pattern = args.pattern
    if args.conversation:
        pattern = f"{conversation_dir(args.conversation)}/{pattern}"
    files = sorted(p for p in run.dir.glob(pattern) if p.is_file())
    ok = True
    if args.expect_count is not None:
        ok = len(files) == args.expect_count
    if args.expect_min is not None:
        ok = ok and len(files) >= args.expect_min
    return {
        "ok": ok,
        "pattern": args.pattern,
        "count": len(files),
        "files": [
            {
                "path": str(p.relative_to(run.dir)),
                "bytes": p.stat().st_size,
                "mode": oct(p.stat().st_mode & 0o777),
            }
            for p in files[:200]
        ],
    }


def cmd_state_cat(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    path = args.path
    if args.conversation:
        path = f"{conversation_dir(args.conversation)}/{path}"
    target = run_relative(run, path)
    if not target.is_file():
        usage_error(
            f"{args.path} does not exist", example=f"{CLI} state ls 'home/.openhands/*'"
        )
    raw = target.read_text(errors="replace")
    try:
        content: Any = json.loads(raw)
    except ValueError:
        content = raw[: args.max_chars]
    checks = [evaluate_check(content, spec) for spec in args.check or []]
    ok = all(c["ok"] for c in checks)
    if args.contains is not None and args.contains not in raw:
        ok = False
    if args.not_contains is not None and args.not_contains in raw:
        ok = False
    if args.mode and oct(target.stat().st_mode & 0o777)[2:] != args.mode.lstrip("0"):
        ok = False
    result: dict[str, Any] = {
        "ok": ok,
        "path": args.path,
        "bytes": len(raw),
        "mode": oct(target.stat().st_mode & 0o777),
        "content": content,
    }
    if checks:
        result["checks"] = checks
    return result


def cmd_state_grep(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    needle = os.environ.get(args.env_value, "") if args.env_value else args.text
    if not needle:
        usage_error("state grep needs TEXT or --env-value NAME (a non-empty variable)")
    REDACT.add(needle, "searched-value")
    hits = []
    for path in sorted(run.dir.glob(args.glob)):
        if (
            path.is_file()
            and not path.is_relative_to(run.private)
            and not path.is_relative_to(run.evidence)
            and not path.is_relative_to(run.dir / "map-run")
        ):
            with contextlib.suppress(OSError):
                if needle.encode() in path.read_bytes():
                    hits.append(str(path.relative_to(run.dir)))
    ok = not hits if args.expect_none else bool(hits)
    return {
        "ok": ok,
        "glob": args.glob,
        "files_with_match": hits[:100],
        "matches": len(hits),
    }


def cmd_config(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    config_path = run.private / "config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else None
    env: dict[str, str] = {}
    if run.mode == "launch":
        env = {
            k: ("<set>" if re.search("KEY|TOKEN|SECRET", k) else v)
            for k, v in server_env(
                run, run.data.get("extra_env", {}), run.data.get("pass_env", [])
            ).items()
            if not FORWARDED_ENV.match(k) or k.startswith(("NO_PROXY", "no_proxy"))
        }
    return {
        "ok": True,
        "run": str(run.dir),
        "mode": run.mode,
        "url": run.data.get("url"),
        "config_file": config,
        "server_env": env,
        "command": run.data.get("command"),
    }


CANVAS_BACKEND = """#!{python}
import json
import sys

from aiohttp import WSMsgType, web

port, data_dir = int(sys.argv[1]), sys.argv[2]
mode = {mode!r}


async def health(request):
    if mode == "unhealthy":
        return web.Response(status=503, text="unhealthy")
    return web.Response(text="ok")


async def socket(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for message in ws:
        if message.type == WSMsgType.TEXT:
            await ws.send_str("echo:" + message.data)
    return ws


async def echo(request):
    body = await request.text()
    return web.json_response({{
        "app": {name!r}, "method": request.method, "path": request.path,
        "query": dict(request.query), "body": body, "data_dir": data_dir,
        "cookie_forwarded": "oh_app_backend_session" in request.headers.get("cookie", ""),
    }})


if mode == "exit":
    sys.exit(3)
app = web.Application()
app.router.add_get("/health", health)
app.router.add_get("/ws", socket)
app.router.add_route("*", "/{{tail:.*}}", echo)
web.run_app(app, host="127.0.0.1", port=port, print=None)
"""


def fixture_canvas_app(run: Run, name: str, backend: str | None) -> dict[str, Any]:
    """An Agent Canvas app: manifest, bundle, icon, and optionally a backend."""
    root = run.fixtures / "canvas" / name
    (root / "dist").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(exist_ok=True)
    (root / "dist" / "index.js").write_text(f"export const qaCanvasApp = {name!r};\n")
    (root / "assets" / "icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"/>\n'
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "name": name,
        "display_name": f"QA {name}",
        "version": "0.1.0",
        "description": "QA fixture Canvas app",
        "entrypoint": "dist/index.js",
        "contributes": {
            "pages": [{"id": "main", "title": f"QA {name}", "path": f"/{name}"}]
        },
        "icon": "assets/icon.svg",
    }
    sha = None
    if backend:
        source = root / "backend" / "src"
        source.mkdir(parents=True, exist_ok=True)
        python = checkout_python(Path(run.data.get("checkout", REPO_ROOT)))[0]
        server = source / "server.py"
        server.write_text(CANVAS_BACKEND.format(python=python, mode=backend, name=name))
        server.chmod(0o755)
        archive = root / "backend" / "linux.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(server, arcname="server.py")
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        artifact = {"path": "backend/linux.tar.gz", "sha256": sha}
        manifest["backend"] = {
            "schema_version": 1,
            "artifacts": {"linux-amd64": artifact, "linux-arm64": artifact},
            "argv": ["{artifact_dir}/server.py", "{port}", "{data_dir}"],
            "health": {
                "path": "/health",
                "timeout_seconds": 15,
                "interval_seconds": 0.1,
            },
        }
    (root / "canvas-extension.json").write_text(json.dumps(manifest, indent=2) + "\n")
    env = {"GIT_CONFIG_GLOBAL": str(run.dir / "home" / ".gitconfig")}
    if not (root / ".git").exists():
        run_cmd(["git", "init", "-q", "-b", "main"], root, env)
    head = (
        git_commit_all(root, run, f"QA canvas app {name}")
        if git("status", "--porcelain", cwd=root)
        else git("rev-parse", "HEAD", cwd=root)
    )
    return {
        "path": str(root),
        "url": f"file://{root}",
        "name": name,
        "head": head,
        "backend": backend,
        "artifact_sha256": sha,
    }


FIXTURES: dict[str, Callable[[Run, str], dict[str, Any]]] = {
    "git-repo": fixture_git_repo,
    "skill": fixture_skill,
    "mcp-server": fixture_mcp_server,
    "plugin": fixture_plugin,
    "git-source": fixture_git_source,
    "marketplace": fixture_marketplace,
    "project": fixture_project,
    "http-sink": fixture_http_sink,
}


def cmd_fixture(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    if args.kind == "list":
        return {
            "ok": True,
            "kinds": sorted(FIXTURES),
            "root": str(run.fixtures),
            "existing": sorted(
                str(p.relative_to(run.fixtures)) for p in run.fixtures.glob("*")
            ),
        }
    if args.kind == "llm-stub":
        info = start_llm_stub(run, args.name or "qa-stub", args.step or [])
        if args.print_path:
            print(info["url"])
            raise SystemExit(0)
        return {"ok": True, "kind": args.kind, **info}
    if args.kind == "canvas-app":
        info = fixture_canvas_app(run, args.name or "qa-canvas", args.backend)
        if args.print_path:
            print(info["path"])
            raise SystemExit(0)
        return {"ok": True, "kind": args.kind, **info}
    if args.kind == "http-sink":
        info = start_http_sink(run, args.name or "sink", args.status, args.body)
        if args.print_path:
            print(info["url"])
            raise SystemExit(0)
        return {"ok": True, "kind": args.kind, **info}
    maker = FIXTURES[args.kind]
    name = args.name or {
        "git-repo": "qa-repo",
        "skill": "qa-skill",
        "mcp-server": "qa-mcp",
        "plugin": "qa-plugin",
        "git-source": "qa-src",
        "marketplace": "qa-market",
        "project": "qa-project",
        "http-sink": "sink",
    }.get(args.kind, "qa")
    info = maker(run, name)
    if args.print_path:
        print(info["path"])
        raise SystemExit(0)
    return {"ok": True, "kind": args.kind, **info}


# --------------------------------------------------------------------------
# exec (SDK / TypeScript client lanes)
# --------------------------------------------------------------------------


def cmd_exec(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        usage_error(
            "exec needs a command after --",
            example=f"{CLI} exec -- uv run python script.py",
        )
    env = {**os.environ, **parse_kv(args.env, "--env")}
    key = run.session_key or ""
    if args.openai:
        env.update(
            {"OPENAI_BASE_URL": f"{run.url}/v1", "OPENAI_API_KEY": key or "none"}
        )
    env.update(
        {
            "AGENT_SERVER_URL": run.url,
            "AGENT_SERVER_SESSION_API_KEY": key,
            "SESSION_API_KEY": key,
            RUN_ENV: str(run.dir),
        }
    )
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=args.cwd or REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
        code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = (
            exc.stdout.decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
    elapsed = int((time.monotonic() - started) * 1000)
    transcript = {
        "command": command,
        "cwd": str(args.cwd or REPO_ROOT),
        "exit_code": code,
        "elapsed_ms": elapsed,
        "stdout": stdout,
        "stderr": stderr,
    }
    result: dict[str, Any] = {
        "ok": code == 0,
        "exit_code": code,
        "elapsed_ms": elapsed,
        "stdout_tail": stdout[-args.tail_chars :],
        "stderr_tail": stderr[-args.tail_chars :],
    }
    missing = [t for t in args.expect_output or [] if t not in stdout + stderr]
    present = [t for t in args.reject_output or [] if t in stdout + stderr]
    if missing or present:
        result["ok"] = False
        result["hint"] = {"missing": missing, "unexpected": present}
    if args.save:
        result["saved"] = str(save_evidence(run, args.save, transcript))
    return result


# --------------------------------------------------------------------------
# evidence
# --------------------------------------------------------------------------


def ledger_path(run: Run) -> Path:
    return run.evidence / "ledger.jsonl"


def read_ledger(run: Run) -> list[dict[str, Any]]:
    path = ledger_path(run)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def cmd_evidence_add(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run, require_alive=False)
    known = {row["id"] for row in collect_ids()}
    if args.feature not in known and not args.allow_unknown:
        usage_error(
            f"{args.feature} is not a sub-feature ID in the map.",
            example=f"{CLI} map ids | grep {args.feature.split('.')[0]}",
        )
    artifacts = []
    for artifact in args.artifact or []:
        path = Path(artifact)
        if not path.is_absolute():
            path = run.dir / artifact
        if not path.exists():
            usage_error(f"artifact {artifact} does not exist")
        artifacts.append(
            str(path.relative_to(run.dir))
            if path.is_relative_to(run.dir)
            else str(path)
        )
    row = append_ledger(
        run,
        args.feature,
        args.result,
        args.entry,
        args.expected,
        args.actual,
        artifacts,
        args.note,
    )
    return {"ok": True, "ledger": str(ledger_path(run)), "row": row}


def latest_rows(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[row["feature"]] = row
    return latest


def cmd_evidence_report(args: argparse.Namespace) -> dict[str, Any]:
    runs = (
        [resolve_run(r, require_alive=False) for r in args.runs]
        if args.runs
        else [resolve_run(args.run, require_alive=False)]
    )
    rows = [row | {"run_dir": str(r.dir)} for r in runs for row in read_ledger(r)]
    latest = latest_rows(rows)
    order = {"fail": 0, "xpass": 1, "blocked": 2, "not-run": 3, "xfail": 4, "pass": 5}
    ordered = sorted(
        latest.values(), key=lambda r: (order.get(r["result"], 9), r["feature"])
    )
    counts: dict[str, int] = {}
    for row in ordered:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    all_ids = [row["id"] for row in collect_ids()]
    missing = [i for i in all_ids if i not in latest]
    lines = [
        "| Feature | Result | Entry point | Expected | Actual | Evidence |",
        "|---|---|---|---|---|---|",
    ]
    for row in ordered:
        cells = [
            row["feature"],
            row["result"],
            row.get("entry") or "",
            row.get("expected") or "",
            row.get("actual") or "",
            ", ".join(row.get("artifacts") or []),
        ]
        lines.append(
            "| "
            + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells)
            + " |"
        )
    markdown = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(REDACT.text(markdown) + "\n")
    return {
        "ok": True,
        "counts": counts,
        "features_with_evidence": len(latest),
        "map_ids_without_evidence": len(missing),
        "missing_sample": missing[:50],
        "markdown": markdown if args.print_markdown else None,
        "out": args.out,
    }


# --------------------------------------------------------------------------
# map
# --------------------------------------------------------------------------


def family_files(only: str | None = None) -> list[Path]:
    files = sorted(
        p for p in FEATURE_MAP_DIR.glob("F*.md") if FAMILY_FILE_RE.match(p.name)
    )
    if only:
        files = [
            p for p in files if p.name == Path(only).name or p.stem.startswith(only)
        ]
        if not files:
            usage_error(f"No family file matches {only}")
    return files


def collect_ids(files: Sequence[Path] | None = None) -> list[dict[str, Any]]:
    rows = []
    for path in files or family_files():
        section = None
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.startswith("## "):
                section = line[3:].strip()
                continue
            if section == "Sub-features":
                match = SUB_FEATURE_RE.match(line)
                if match:
                    rows.append(
                        {"id": match.group(1), "file": path.name, "line": number}
                    )
    return rows


def route_templates(checkout: Path | None = None) -> list[dict[str, Any]]:
    """Every HTTP and WebSocket route of the app, by importing it in a subprocess."""
    checkout = checkout or REPO_ROOT
    code = (
        "import json, logging\n"
        "logging.disable(logging.CRITICAL)\n"
        "from fastapi.routing import APIRoute, APIWebSocketRoute\n"
        "from starlette.routing import Route\n"
        "from openhands.agent_server.api import create_app\n"
        "from openhands.agent_server.config import get_default_config\n"
        "def table(app):\n"
        "    out = []\n"
        "    for r in app.routes:\n"
        "        if isinstance(r, APIRoute):\n"
        "            for m in sorted(r.methods - {'HEAD', 'OPTIONS'}):\n"
        "                out.append({'method': m, 'path': r.path, 'name': r.name,\n"
        "                            'module': r.endpoint.__module__.rsplit('.', 1)[-1],\n"
        "                            'deprecated': bool(r.deprecated)})\n"
        "        elif isinstance(r, APIWebSocketRoute):\n"
        "            out.append({'method': 'WS', 'path': r.path, 'name': r.name,\n"
        "                        'module': r.endpoint.__module__.rsplit('.', 1)[-1],\n"
        "                        'deprecated': False})\n"
        "        elif isinstance(r, Route) and r.methods:\n"
        "            for m in sorted(r.methods - {'HEAD', 'OPTIONS'}):\n"
        "                out.append({'method': m, 'path': r.path, 'name': r.name,\n"
        "                            'module': 'fastapi', 'deprecated': False})\n"
        "    return out\n"
        "out = table(create_app())\n"
        "seen = {(r['method'], r['path']) for r in out}\n"
        "docker = get_default_config().model_copy(update={'conversation_runtime': 'docker'})\n"
        "for r in table(create_app(docker)):\n"
        "    if (r['method'], r['path']) not in seen:\n"
        "        out.append({**r, 'config': 'conversation_runtime=docker'})\n"
        "print('ROUTES-JSON' + json.dumps(out))\n"
    )
    env = {
        **os.environ,
        "OH_SESSION_API_KEYS_0": "map-routes",
        "HOME": str(runs_home() / ".map-home"),
        "OH_PERSISTENCE_DIR": str(runs_home() / ".map-home" / ".openhands"),
    }
    completed = subprocess.run(
        [*checkout_python(checkout), "-c", code],
        cwd=runs_home_ensure(),
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    for line in completed.stdout.splitlines():
        if line.startswith("ROUTES-JSON"):
            routes = json.loads(line[len("ROUTES-JSON") :])
            seen: set[tuple[str, str]] = set()
            unique = []
            for route in routes:
                key = (route["method"], route["path"])
                if key not in seen:
                    seen.add(key)
                    unique.append(route)
            return unique
    env_error(
        "Could not import the agent-server app to list routes.",
        hint=completed.stderr[-800:],
    )


def runs_home_ensure() -> Path:
    home = runs_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


ROUTE_LINE_RE = re.compile(r"`(GET|POST|PUT|PATCH|DELETE|WS) (/[^`\s]*)`")
API_CMD_RE = re.compile(
    rf"{CLI} api (GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD) '?\"?(/[^\s'\"`]*)"
)
WS_CMD_RE = re.compile(rf"{CLI} ws (?:listen|start) '?\"?(/[^\s'\"`]*)")
CMD_START_RE = re.compile(rf"(?:^|[`\s($|&;]){CLI} ")


def extract_commands(text: str) -> list[str]:
    """Every `control-agent-server ...` invocation in markdown text.

    Finds commands in inline code, fenced blocks, `$(...)` substitutions and
    `&&` chains, stopping at the end of the shell word sequence.
    """
    commands = []
    for match in CMD_START_RE.finditer(text):
        start = match.end() - len(CLI) - 1
        quote = ""
        depth = 0
        i = start
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "'\"":
                quote = ch
            elif ch == "\\" and text[i + 1 : i + 2] == "\n":
                i += 2
                continue
            elif ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif ch in "`\n;|" or text.startswith("&&", i):
                break
            elif ch == "&" and text[i - 1] not in "<>&" and text[i + 1 : i + 2] != ">":
                break
            i += 1
        commands.append(text[start:i].replace("\\\n", " ").strip())
    return commands


def template_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", template)
    pattern = ""
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            pattern += ".+" if part.endswith(":path}") else "[^/]+"
        else:
            pattern += re.escape(part)
    return re.compile(f"^{pattern}/?$")


SHELL_EXPANSION_RE = re.compile(r"\$\{[^}]*\}")


def match_route(
    method: str, concrete: str, routes: Sequence[dict[str, Any]]
) -> dict[str, Any] | None:
    concrete = SHELL_EXPANSION_RE.sub("X", concrete.split("?", 1)[0])
    # Config-only catch-all proxies (the Docker runtime's {tail:path}) would
    # match any typo, so recipe paths never resolve to them.
    candidates = [
        r
        for r in routes
        if r["method"] == method
        and not (r.get("config") and r["path"].endswith(":path}"))
        and template_regex(r["path"]).match(concrete)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda r: r["path"].count("{"))


def owned_routes(path: Path) -> list[tuple[str, str, int]]:
    owned = []
    in_routes = False
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.startswith("Routes:"):
            in_routes = True
        elif in_routes and (line.startswith("## ") or not line.strip()):
            in_routes = False
        if in_routes:
            for method, route in ROUTE_LINE_RE.findall(line):
                owned.append((method, route, number))
    return owned


def driven_routes(path: Path, routes: Sequence[dict[str, Any]]) -> set[tuple[str, str]]:
    text = path.read_text()
    driven: set[tuple[str, str]] = set()
    for method, concrete in API_CMD_RE.findall(text):
        match = match_route(method, concrete, routes)
        if match:
            driven.add((match["method"], match["path"]))
    for concrete in WS_CMD_RE.findall(text):
        match = match_route("WS", concrete, routes)
        if match:
            driven.add(("WS", match["path"]))
    return driven


REDIRECT_RE = re.compile(r"^\d?>>?(&\d)?$|^\d?<$")
REDIRECT_WITH_TARGET_RE = re.compile(r"^\d?>>?\S+$")


def drop_redirections(argv: list[str]) -> list[str]:
    """Remove shell redirections (`> file`, `2>&1`, `>/dev/null`) from argv."""
    kept: list[str] = []
    skip = False
    for token in argv:
        if skip:
            skip = False
        elif REDIRECT_RE.match(token):
            skip = not token.endswith(("&1", "&2"))
        elif not REDIRECT_WITH_TARGET_RE.match(token):
            kept.append(token)
    return kept


def validate_command(command: str) -> str | None:
    """Parse one recipe command with the real parser, without executing it."""
    text = command.strip()
    if text.endswith("`"):
        text = text[:-1]
    try:
        argv = shlex.split(re.sub(r"<([A-Za-z_][\w-]*)>", r"\1", text), comments=True)
    except ValueError as exc:
        return f"unparseable: {exc}"
    argv = drop_redirections(argv[1:])
    parser = recipe_parser()
    stderr = io.StringIO()
    try:
        with (
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code not in (0, None):
            message = (
                stderr.getvalue().strip().splitlines()[-1]
                if stderr.getvalue()
                else "invalid"
            )
            # A value that comes from the shell ('$N', '$(...)') is only known
            # at run time, so a type or choice error about it is not a defect.
            if "invalid" in message and "'$" in message:
                return None
            return message
    return None


def check_family(path: Path, routes: Sequence[dict[str, Any]] | None) -> list[str]:
    problems: list[str] = []
    text = path.read_text()
    lines = text.splitlines()
    family = "F" + FAMILY_FILE_RE.match(path.name).group(1)  # type: ignore[union-attr]
    if not lines or not lines[0].startswith("# "):
        problems.append("first line must be an H1 title")
    if not any(line.startswith("Source:") for line in lines):
        problems.append("missing `Source:` line")
    if not any(line.startswith("Routes:") for line in lines):
        problems.append(
            "missing `Routes:` line (use `Routes: none` for behavior-only families)"
        )
    h2 = [line[3:].strip() for line in lines if line.startswith("## ")]
    expected = [
        "Sub-features",
        "How to get to it (agent POV)",
        f"Driving it with {CLI}",
        "Gotchas",
    ]
    if h2 != expected:
        problems.append(f"H2 sections must be exactly {expected}, got {h2}")
    ids = collect_ids([path])
    if not ids:
        problems.append("no sub-feature IDs under ## Sub-features")
    for row in ids:
        if not row["id"].startswith(family + "."):
            problems.append(
                f"line {row['line']}: {row['id']} does not start with {family}."
            )
    driving = text.split(f"## Driving it with {CLI}", 1)[-1].split("## Gotchas", 1)[0]
    if "Preconditions:" not in driving:
        problems.append("Driving section must start with `Preconditions:`")
    for row in ids:
        if f"`{row['id']}`" not in driving and f"({row['id']})" not in driving:
            problems.append(f"{row['id']} is never referenced in the Driving section")
    unknown_needs = [n for n in family_needs(path) if n not in NEEDS]
    if unknown_needs:
        problems.append(
            f"Needs: unknown tokens {unknown_needs}; use {', '.join(NEEDS)}"
        )
    flags = launch_flags(path)
    if flags:
        error = validate_command(
            " ".join([CLI, "launch", "--new", *map(shlex.quote, flags)])
        )
        if error:
            problems.append(f"Launch: flags do not parse: {error}")
    in_driving = False
    in_fence = False
    for number, line in enumerate(lines, 1):
        if line.startswith("## "):
            in_driving = line == f"## Driving it with {CLI}"
        if not in_driving:
            continue
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence and line.strip().startswith("! "):
            problems.append(
                f"line {number}: `! cmd` never fails under set -e; use "
                "`if cmd; then false; fi` or a --check/--expect assertion"
            )
        if not in_fence and line.startswith("- **") and line.count("**") < 2:
            problems.append(
                f"line {number}: bold bullet label is not closed on its own line; "
                "map run would merge this bullet into the previous one"
            )
    for bullet in driving_bullets(path):
        blocked = "blocked" in bullet.label.lower()
        if (
            bullet.label != "Preconditions"
            and not bullet.script.strip()
            and not blocked
        ):
            problems.append(
                f"line {bullet.line}: bullet '{bullet.label}' has no ```sh block"
            )
        if bullet.label != "Preconditions" and not bullet.ids:
            problems.append(
                f"line {bullet.line}: bullet '{bullet.label}' names no sub-feature ID"
            )
        known_bug = "known bug" in bullet.label.lower()
        marked = bool(bug_ranges(bullet.script))
        if known_bug and not blocked and not marked:
            problems.append(
                f"line {bullet.line}: known-bug bullet '{bullet.label}' marks no "
                "command with `# bug`; end the line of the assertion that fails "
                "because of the bug with `# bug`"
            )
        if marked and not known_bug:
            problems.append(
                f"line {bullet.line}: bullet '{bullet.label}' has a `# bug` marker "
                "but its label does not say known bug"
            )
    for command in extract_commands(text):
        error = validate_command(command)
        if error:
            problems.append(f"invalid command `{command}`: {error}")
    if routes is not None:
        for method, route, number in owned_routes(path):
            if not any(r["method"] == method and r["path"] == route for r in routes):
                problems.append(
                    f"line {number}: Routes lists {method} {route}, which the app does not serve"
                )
        http_methods = {r["method"] for r in routes} - {"WS"}
        for method, concrete in API_CMD_RE.findall(text):
            # HEAD and OPTIONS (CORS preflight) are answered on any served path.
            candidates = http_methods if method in ("HEAD", "OPTIONS") else {method}
            if not any(match_route(m, concrete, routes) for m in candidates):
                problems.append(
                    f"recipe calls {method} {concrete}, which matches no route"
                )
    return problems


def parse_index() -> list[dict[str, Any]]:
    index = FEATURE_MAP_DIR / "README.md"
    rows = []
    if not index.exists():
        return rows
    for line in index.read_text().splitlines():
        match = re.match(
            r"^\| (F\d{2}) \| \[([^\]]+)\]\(([^)]+)\) \|(.*)\| (\d+) \|$", line
        )
        if match:
            rows.append(
                {
                    "id": match.group(1),
                    "title": match.group(2),
                    "file": match.group(3),
                    "count": int(match.group(5)),
                    "line": line,
                }
            )
    return rows


def cmd_map_check(args: argparse.Namespace) -> dict[str, Any]:
    files = family_files(args.file)
    routes = None if args.no_routes else route_templates()
    problems: dict[str, list[str]] = {}
    for path in files:
        found = check_family(path, routes)
        if found:
            problems[path.name] = found
    all_ids = collect_ids()
    seen: dict[str, str] = {}
    for row in all_ids:
        if row["id"] in seen and seen[row["id"]] != row["file"]:
            problems.setdefault(row["file"], []).append(
                f"duplicate ID {row['id']} (also in {seen[row['id']]})"
            )
        seen[row["id"]] = row["file"]
    counts: dict[str, int] = {}
    for row in all_ids:
        counts[row["file"]] = counts.get(row["file"], 0) + 1
    index_rows = parse_index()
    if not args.file:
        listed = {row["file"] for row in index_rows}
        for path in family_files():
            if path.name not in listed:
                problems.setdefault("README.md", []).append(
                    f"{path.name} is not in the index table"
                )
        for row in index_rows:
            if not (FEATURE_MAP_DIR / row["file"]).exists():
                problems.setdefault("README.md", []).append(
                    f"index links missing file {row['file']}"
                )
            elif counts.get(row["file"], 0) != row["count"]:
                if args.fix_counts:
                    fix_index_count(row, counts.get(row["file"], 0))
                else:
                    problems.setdefault("README.md", []).append(
                        f"{row['file']}: index says {row['count']} sub-features, file has {counts.get(row['file'], 0)}"
                        " (run `map check --fix-counts`)"
                    )
        if args.fix_counts:
            fix_index_total(len(all_ids))
        total_line = re.search(
            r"\*\*(\d+) sub-features\*\*",
            (FEATURE_MAP_DIR / "README.md").read_text()
            if (FEATURE_MAP_DIR / "README.md").exists()
            else "",
        )
        if (
            total_line
            and int(total_line.group(1)) != len(all_ids)
            and not args.fix_counts
        ):
            problems.setdefault("README.md", []).append(
                f"total says {total_line.group(1)}, map has {len(all_ids)} (run `map check --fix-counts`)"
            )
    return {
        "ok": not problems,
        "files": len(files),
        "ids": len(all_ids),
        "problems": problems,
    }


def fix_index_count(row: dict[str, Any], count: int) -> None:
    index = FEATURE_MAP_DIR / "README.md"
    new_line = re.sub(r"\| \d+ \|$", f"| {count} |", row["line"])
    index.write_text(index.read_text().replace(row["line"], new_line))


def fix_index_total(total: int) -> None:
    index = FEATURE_MAP_DIR / "README.md"
    if index.exists():
        index.write_text(
            re.sub(
                r"\*\*\d+ sub-features\*\*",
                f"**{total} sub-features**",
                index.read_text(),
            )
        )


def cmd_map_coverage(args: argparse.Namespace) -> dict[str, Any]:
    routes = route_templates(Path(args.checkout).resolve() if args.checkout else None)
    owners: dict[tuple[str, str], list[str]] = {}
    driven: set[tuple[str, str]] = set()
    for path in family_files():
        for method, route, _ in owned_routes(path):
            owners.setdefault((method, route), []).append(path.name)
        driven |= driven_routes(path, routes)
    excluded = parse_exclusions()
    unowned, undriven, multi = [], [], []
    for route in routes:
        key = (route["method"], route["path"])
        label = f"{route['method']} {route['path']}"
        if key not in owners and label not in excluded:
            unowned.append(label)
        elif key in owners and key not in driven and label not in excluded:
            undriven.append({"route": label, "owner": owners[key]})
        if len(owners.get(key, [])) > 1:
            multi.append({"route": label, "owners": owners[key]})
    stale = [
        e
        for e in excluded
        if not any(f"{r['method']} {r['path']}" == e for r in routes)
    ]
    return {
        "ok": not unowned and not multi and not stale,
        "routes": len(routes),
        "owned": len([r for r in routes if (r["method"], r["path"]) in owners]),
        "driven": len(driven),
        "unowned": unowned,
        "owned_but_not_driven": undriven,
        "multiple_owners": multi,
        "stale_exclusions": stale,
    }


def parse_exclusions() -> set[str]:
    index = FEATURE_MAP_DIR / "README.md"
    if not index.exists():
        return set()
    section = index.read_text().split("## Not mapped", 1)
    if len(section) < 2:
        return set()
    body = section[1].split("\n## ", 1)[0]
    return {f"{m} {p}" for m, p in ROUTE_LINE_RE.findall(body)}


BULLET_RE = re.compile(r"^- \*\*(.+?)\*\*")
BUG_MARK_RE = re.compile(r"\s#\s*bug\s*$")
FENCE_RE = re.compile(r"^\s*```(\w*)\s*$")
ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)


@dataclass
class Bullet:
    label: str
    ids: list[str]
    script: str
    line: int


def bug_ranges(script: str) -> list[tuple[int, int]]:
    """Line ranges (1-based, in the bullet script) of commands marked `# bug`.

    The marker ends the command's last line; a command continued with `\\`
    starts at the first line of the continuation.
    """
    lines = script.splitlines()
    ranges = []
    for number, line in enumerate(lines, 1):
        if BUG_MARK_RE.search(line):
            first = number
            while first > 1 and lines[first - 2].rstrip().endswith("\\"):
                first -= 1
            ranges.append((first, number))
    return ranges


def driving_bullets(path: Path) -> list[Bullet]:
    """The fenced `sh` blocks of the Driving section, grouped by bullet.

    Shell blocks under `Preconditions:` form a first pseudo-bullet labelled
    `Preconditions`. Prose and inline code are never executed.
    """
    lines = path.read_text().splitlines()
    try:
        start = lines.index(f"## Driving it with {CLI}")
    except ValueError:
        return []
    bullets: list[Bullet] = []
    current = Bullet("Preconditions", [], "", start + 1)
    in_fence = False
    fence_lines: list[str] = []
    for number, line in enumerate(lines[start + 1 :], start + 2):
        if line.startswith("## "):
            break
        fence = FENCE_RE.match(line)
        if fence and not in_fence:
            in_fence = fence.group(1) in ("sh", "bash", "shell")
            fence_lines = []
            if not in_fence and fence.group(1):
                in_fence = False
            continue
        if fence and in_fence:
            indent = min(
                (len(x) - len(x.lstrip()) for x in fence_lines if x.strip()), default=0
            )
            current.script += "\n".join(x[indent:] for x in fence_lines) + "\n"
            in_fence = False
            continue
        if in_fence:
            fence_lines.append(line)
            continue
        match = BULLET_RE.match(line)
        if match:
            if current.script or current.label != "Preconditions":
                bullets.append(current)
            label = match.group(1)
            current = Bullet(
                label, re.findall(r"F\d{2}\.[a-z0-9-]+", label), "", number
            )
    if current.script or current.label != "Preconditions":
        bullets.append(current)
    return bullets


NEEDS = (
    "llm",
    "tmux",
    "git",
    "node",
    "uvx",
    "docker",
    "chromium",
    "vscode",
    "network",
)


def family_needs(path: Path) -> list[str]:
    """Tokens from the family's optional `Needs:` line."""
    for line in path.read_text().splitlines():
        if line.startswith("Needs:"):
            return [t for t in re.split(r"[\s,`]+", line[len("Needs:") :]) if t]
    return []


def network_reachable(url: str) -> tuple[bool, str]:
    """Outbound HTTPS, for recipes that need github.com or a provider's auth host."""
    try:
        httpx.head(url, timeout=5, follow_redirects=False)
    except httpx.HTTPError as exc:
        return False, f"{url} unreachable: {type(exc).__name__}"
    return True, f"{url} reachable"


def machine_capabilities() -> dict[str, dict[str, Any]]:
    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent"))
    chromium = (
        shutil.which("chromium")
        or shutil.which("google-chrome")
        or next((str(p) for p in browsers.glob("chromium*")), None)
    )
    vscode = shutil.which("openvscode-server") or next(
        (str(p) for p in Path("/openhands").glob(".openvscode-server*")), None
    )
    docker = Path("/var/run/docker.sock").exists() or bool(
        os.environ.get("DOCKER_HOST")
    )
    key = next(
        (n for n in ("DEEPSEEK_API_KEY", "LLM_API_KEY") if os.environ.get(n)), None
    )
    found = {
        "llm": (key is not None, key or "set DEEPSEEK_API_KEY"),
        "tmux": (bool(shutil.which("tmux")), shutil.which("tmux")),
        "git": (bool(shutil.which("git")), shutil.which("git")),
        "node": (
            bool(shutil.which("node") and shutil.which("npm")),
            shutil.which("node"),
        ),
        "uvx": (bool(shutil.which("uvx")), shutil.which("uvx")),
        "docker": (docker, "docker daemon socket" if docker else "no docker daemon"),
        "chromium": (chromium is not None, chromium),
        "vscode": (vscode is not None, vscode or "no openvscode-server binary"),
        "network": network_reachable("https://github.com"),
    }
    return {
        name: {"available": ok, "detail": detail}
        for name, (ok, detail) in found.items()
    }


def cmd_capabilities(args: argparse.Namespace) -> dict[str, Any]:
    del args
    machine = machine_capabilities()
    families = []
    for path in family_files():
        needs = family_needs(path)
        missing = [n for n in needs if not machine.get(n, {}).get("available")]
        families.append(
            {
                "file": path.name,
                "needs": needs,
                "missing": missing,
                "drivable": not missing,
            }
        )
    return {
        "ok": True,
        "machine": machine,
        "families": families,
        "blocked_families": [f["file"] for f in families if f["missing"]],
    }


def launch_flags(path: Path) -> list[str]:
    """Flags from the family's optional `Launch:` line (for `map run --fresh`)."""
    for line in path.read_text().splitlines():
        if line.startswith("Launch:"):
            return shlex.split(line[len("Launch:") :].replace("`", " "))
    return []


def self_command(*argv: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *argv],
        capture_output=True,
        text=True,
    )
    lines = completed.stdout.strip().splitlines()
    if completed.returncode != 0 or not lines:
        raise CliError(
            f"`{CLI} {' '.join(argv)}` failed",
            exit_code=3,
            data={
                "stdout": completed.stdout[-1500:],
                "stderr": completed.stderr[-1500:],
            },
        )
    return {"stdout": completed.stdout, "last_line": lines[-1]}


def cmd_map_run(args: argparse.Namespace) -> dict[str, Any]:
    if args.all:
        if not args.fresh:
            usage_error(
                "--all needs --fresh (one fresh run per family)",
                example=f"{CLI} map run --all --fresh --record",
            )
        reports = []
        machine = machine_capabilities()
        for path in family_files():
            missing = [
                n for n in family_needs(path) if not machine.get(n, {}).get("available")
            ]
            if missing:
                reports.append(
                    {
                        "ok": True,
                        "file": path.name,
                        "summary": None,
                        "run": None,
                        "error": None,
                        "blocked": missing,
                    }
                )
                continue
            family_args = argparse.Namespace(
                **{**vars(args), "all": False, "file": path.stem}
            )
            try:
                report = cmd_map_run(family_args)
            except CliError as exc:
                report = {"ok": False, "file": path.name, "error": str(exc), **exc.data}
            reports.append(
                {k: report.get(k) for k in ("ok", "file", "summary", "run", "error")}
            )
        blocked = [r["file"] for r in reports if r.get("blocked")]
        failing = [r["file"] for r in reports if not r["ok"]]
        return {
            "ok": not failing,
            "families": len(reports),
            "failing": failing,
            "blocked_by_machine": blocked,
            "reports": reports,
        }
    if not args.file:
        usage_error(
            "map run needs --file (or --all --fresh)",
            example=f"{CLI} map run --file F01",
        )
    files = family_files(args.file)
    if len(files) != 1:
        usage_error(
            "map run needs exactly one --file", example=f"{CLI} map run --file F01"
        )
    path = files[0]
    if not args.fresh:
        return replay_family(resolve_run(args.run), path, args)
    flags = launch_flags(path)
    if args.checkout:
        flags = [*flags, "--checkout", str(Path(args.checkout).resolve())]
    attempts = []
    for _ in range(max(1, args.repeat)):
        launched = self_command(
            "launch",
            "--new",
            "--name",
            f"map-{path.stem[:3].lower()}",
            "--print-run",
            *flags,
        )
        run_dir = launched["last_line"].strip()
        try:
            report = replay_family(load_run(Path(run_dir)), path, args)
        finally:
            self_command("stop", "--run", run_dir)
        attempts.append({**report, "run": run_dir})
    if len(attempts) == 1:
        return {**attempts[0], "launch_flags": flags}
    return {
        "ok": all(a["ok"] for a in attempts),
        "file": path.name,
        "launch_flags": flags,
        "attempts": [
            {
                "run": a["run"],
                "ok": a["ok"],
                "summary": a["summary"],
                "statuses": {b["bullet"]: b.get("status") for b in a["bullets"]},
            }
            for a in attempts
        ],
    }


def replay_family(run: Run, path: Path, args: argparse.Namespace) -> dict[str, Any]:
    bullets = driving_bullets(path)
    if args.only:
        wanted = set(args.only.split(","))
        bullets = [
            b for b in bullets if wanted & set(b.ids) or b.label == "Preconditions"
        ]
    state = run.dir / "map-run" / f"{path.stem}.state.sh"
    state.parent.mkdir(exist_ok=True)
    state.write_text("")
    env = {
        **os.environ,
        RUN_ENV: str(run.dir),
        "PATH": f"{SCRIPT_DIR}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    results = []
    failed = False
    xpassed = False
    for index, bullet in enumerate(bullets):
        marker = bullet.label.lower()
        if "blocked" in marker:
            results.append(
                {"bullet": bullet.label, "ids": bullet.ids, "status": "blocked"}
            )
            if args.record:
                for feature in bullet.ids:
                    append_ledger(
                        run,
                        feature,
                        "blocked",
                        f"map run {path.name}: {bullet.label}",
                        "prerequisite available",
                        "blocked (see the bullet)",
                        [],
                        "bullet is marked blocked in the map",
                    )
            continue
        if not bullet.script.strip():
            results.append(
                {"bullet": bullet.label, "ids": bullet.ids, "status": "no-commands"}
            )
            continue
        if failed and not args.keep_going:
            results.append(
                {"bullet": bullet.label, "ids": bullet.ids, "status": "skipped"}
            )
            continue
        names = sorted(set(ASSIGN_RE.findall(bullet.script)))
        save_state = (
            f"\ndeclare -p {' '.join(names)} >> {shlex_quote(str(state))} 2>/dev/null || true\n"
            if names
            else ""
        )
        failure = run.dir / "map-run" / f"{path.stem}.failed"
        failure.unlink(missing_ok=True)
        preamble = [
            "set -eo pipefail",
            f"source {shlex_quote(str(state))}",
            f"cd {shlex_quote(str(REPO_ROOT))}",
            'trap \'printf "%s\\t%s\\n" "$LINENO" "$BASH_COMMAND" > '
            + shlex_quote(str(failure))
            + "' ERR",
        ]
        script = "\n".join(preamble) + f"\n{bullet.script}{save_state}"
        if args.dry_run:
            results.append(
                {"bullet": bullet.label, "ids": bullet.ids, "script": bullet.script}
            )
            continue
        started = time.monotonic()
        try:
            completed = subprocess.run(
                ["bash", "-c", script],
                env=env,
                capture_output=True,
                text=True,
                timeout=args.timeout,
            )
            code, out, err = completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired:
            code, out, err = 124, "", f"timed out after {args.timeout}s"
        elapsed = int((time.monotonic() - started) * 1000)
        failed_line: int | None = None
        failed_command: str | None = None
        if code != 0 and failure.exists():
            line_text, _, failed_command = (
                failure.read_text().rstrip("\n").partition("\t")
            )
            if line_text.isdigit():
                failed_line = int(line_text) - len(preamble)
            failed_command = REDACT.text(failed_command)[:500]
        transcript = {
            "file": path.name,
            "bullet": bullet.label,
            "ids": bullet.ids,
            "line": bullet.line,
            "script": bullet.script,
            "exit_code": code,
            "elapsed_ms": elapsed,
            "failed_line": failed_line,
            "failed_command": failed_command,
            "stdout": out,
            "stderr": err,
        }
        slug = re.sub(r"[^a-z0-9]+", "-", bullet.label.lower()).strip("-")[:40]
        saved = save_evidence(
            run, f"map-run/{path.stem}-{index:02d}-{slug}", transcript
        )
        known_bug = "known bug" in marker
        ranges = bug_ranges(bullet.script)
        outside = (
            code != 0
            and failed_line is not None
            and bool(ranges)
            and not any(first <= failed_line <= last for first, last in ranges)
        )
        if known_bug and outside:
            status = "fail"
            failed = True
        elif known_bug:
            status = "xfail" if code != 0 else "xpass"
            xpassed = xpassed or code == 0
        else:
            status = "pass" if code == 0 else "fail"
            failed = failed or code != 0
        row: dict[str, Any] = {
            "bullet": bullet.label,
            "ids": bullet.ids,
            "line": bullet.line,
            "status": status,
            "exit_code": code,
            "elapsed_ms": elapsed,
            "transcript": str(saved),
        }
        if code != 0:
            row["failed_line"] = failed_line
            row["failed_command"] = failed_command
            row["stdout_tail"] = REDACT.text(out[-1200:])
            row["stderr_tail"] = REDACT.text(err[-1200:])
        if outside:
            row["hint"] = (
                "a known-bug bullet failed outside its `# bug` assertion: "
                "the arrange steps broke, so the bug was not reproduced"
            )
        if status == "xpass":
            row["hint"] = "the known bug no longer reproduces: update the map"
        results.append(row)
        if args.record and bullet.ids:
            note = {
                "xfail": "known bug reproduced (expected failure)",
                "xpass": "known bug no longer reproduces; update the map",
            }.get(
                status, "automated map run; prose-only expectations were not re-judged"
            )
            if failed_command:
                note += f"; failed at line {failed_line}: {failed_command}"
            for feature in bullet.ids:
                append_ledger(
                    run,
                    feature,
                    status,
                    f"map run {path.name}: {bullet.label}",
                    "every command in the bullet exits 0 (its --expect/--check assertions hold)",
                    f"exit {code}",
                    [str(saved.relative_to(run.dir))],
                    note,
                )
    summary = {
        status: sum(1 for r in results if r.get("status") == status)
        for status in (
            "pass",
            "fail",
            "xfail",
            "xpass",
            "blocked",
            "skipped",
            "no-commands",
        )
    }
    return {
        "ok": not failed and not xpassed,
        "file": path.name,
        "summary": summary,
        "bullets": results,
    }


def append_ledger(
    run: Run,
    feature: str,
    result: str,
    entry: str,
    expected: str,
    actual: str,
    artifacts: list[str],
    note: str | None,
) -> dict[str, Any]:
    row = {
        "at": now_iso(),
        "run": run.id,
        "checkout_sha": run.data.get("checkout_sha"),
        "feature": feature,
        "result": result,
        "entry": entry,
        "expected": expected,
        "actual": actual,
        "artifacts": artifacts,
        "note": note,
    }
    run.evidence.mkdir(exist_ok=True)
    with ledger_path(run).open("a") as handle:
        handle.write(json.dumps(REDACT.obj(row)) + "\n")
    return row


def shlex_quote(value: str) -> str:
    return shlex.quote(value)


def cmd_map_ids(args: argparse.Namespace) -> dict[str, Any]:
    rows = collect_ids(family_files(args.file) if args.file else None)
    return {"ok": True, "count": len(rows), "ids": rows}


def cmd_map_routes(args: argparse.Namespace) -> dict[str, Any]:
    routes = route_templates(Path(args.checkout).resolve() if args.checkout else None)
    if args.module:
        routes = [r for r in routes if r["module"] == args.module]
    return {"ok": True, "count": len(routes), "routes": routes}


def cmd_map_diff(args: argparse.Namespace) -> dict[str, Any]:
    base = {
        (r["method"], r["path"]): r for r in route_templates(Path(args.base).resolve())
    }
    target_checkout = Path(args.target).resolve() if args.target else None
    target = {(r["method"], r["path"]): r for r in route_templates(target_checkout)}
    owners: dict[tuple[str, str], str] = {}
    for path in family_files():
        for method, route, _ in owned_routes(path):
            owners[(method, route)] = path.name
    added = [f"{m} {p}" for (m, p) in sorted(set(target) - set(base))]
    removed = [
        {"route": f"{m} {p}", "owner": owners.get((m, p))}
        for (m, p) in sorted(set(base) - set(target))
    ]
    newly_deprecated = [
        {"route": f"{m} {p}", "owner": owners.get((m, p))}
        for (m, p) in sorted(set(base) & set(target))
        if target[(m, p)]["deprecated"] and not base[(m, p)]["deprecated"]
    ]
    return {
        "ok": True,
        "base": args.base,
        "target": args.target or str(REPO_ROOT),
        "added": added,
        "removed": removed,
        "newly_deprecated": newly_deprecated,
    }


def source_prefixes(path: Path) -> list[str]:
    prefixes = []
    for line in path.read_text().splitlines():
        if line.startswith("Source:"):
            prefixes += re.findall(r"`([^`]+)`", line)
    return prefixes


def cmd_map_owners(args: argparse.Namespace) -> dict[str, Any]:
    if args.changed:
        diff = git("diff", "--name-only", args.changed)
        if diff == "unknown":
            usage_error(
                f"git diff {args.changed} failed (shallow clone? fetch more history)"
            )
        paths = [p for p in diff.splitlines() if p]
    else:
        paths = list(args.paths)
    prefixes = {f.name: source_prefixes(f) for f in family_files()}
    mapping, unowned = [], []
    for changed in paths:
        owners = [
            name
            for name, pre in prefixes.items()
            if any(changed.startswith(p.rstrip("*")) for p in pre)
        ]
        if owners:
            mapping.append({"path": changed, "families": owners})
        elif re.match(
            r"^(openhands-agent-server|openhands-sdk|openhands-tools|openhands-workspace|clients/typescript)/",
            changed,
        ):
            unowned.append(changed)
    return {
        "ok": True,
        "changed": len(paths),
        "mapped": mapping,
        "unowned_product_paths": unowned,
    }


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

TOP_HELP = f"""\
{CLI}: drive an isolated OpenHands Agent Server through its REST and
WebSocket API and capture evidence. Every command prints one JSON object.
Exit codes: 0 ok, 1 action failed, 2 usage, 3 environment.

Lifecycle:
  launch        Start an isolated server from this checkout (private HOME, state, key, port)
  attach        Drive an existing server (Docker image, binary, remote sandbox) instead
  doctor        Read-only health check; run it first and after every surprise
  restart       Restart the same run (persistence, reconnection, --rotate-key)
  stop          Stop only this run's processes; evidence survives (--purge-private)
  runs          List runs with liveness
  logs          Tail or grep the server log (secrets redacted)

Driving:
  api           Call any REST endpoint: api METHOD PATH [--json ...] [--expect 200]
  ws            listen | start | read | stop: WebSocket capture with auth modes
  llm           preset deepseek | set | show: LLM profiles from an env key
  conversation  start | wait | send | events: the essential agent pathways
  fixture       git-repo | project | skill | plugin | git-source | marketplace |
                canvas-app | mcp-server | http-sink | llm-stub | list: run-owned fixtures
  sink          read: what an HTTP sink (webhooks, telemetry, credentials) received
  state         ls | cat | grep: files the server persisted (settings, secrets, conversations)
  config        the run's config file and server environment, secrets masked
  capabilities  what this machine can drive (keys, binaries) and which families are blocked
  exec          Run an SDK/TypeScript-client program against the run (env injected)

Proof and map:
  evidence      add | report: the append-only ledger under <run>/evidence/
  map           run | check | coverage | ids | routes | diff | owners: an executable, checked map

Which run: --run DIR, else ${RUN_ENV}, else the only live run.
Run state lives under ${HOME_ENV} (default $TMPDIR/agent-server-verify).

Examples:
  export {RUN_ENV}=$({CLI} launch --new --print-run)
  {CLI} doctor
  {CLI} llm preset deepseek
  CID=$({CLI} conversation start --prompt 'Create hello.txt containing hi' --wait --print-id)
  {CLI} conversation events "$CID" --kinds ActionEvent,ObservationEvent
  {CLI} api GET /api/conversations/count --expect 200 --save F03.count/count
  {CLI} evidence add --feature F03.count --result pass --entry 'GET /api/conversations/count' \\
      --expected '1' --actual '1' --artifact evidence/F03.count/count.json
  {CLI} stop
"""


class Formatter(argparse.RawDescriptionHelpFormatter):
    pass


def add_run_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--run",
        default=argparse.SUPPRESS,
        help=f"run directory (default: ${RUN_ENV} or the only live run)",
    )


def sub(
    subparsers: Any,
    name: str,
    help_text: str,
    examples: str,
    func: Callable[[argparse.Namespace], dict[str, Any]],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        name,
        help=help_text,
        description=help_text,
        epilog="Examples:\n" + examples,
        formatter_class=Formatter,
    )
    add_run_flag(parser)
    parser.set_defaults(func=func)
    return parser


@functools.cache
def recipe_parser() -> argparse.ArgumentParser:
    """One parser for validating every recipe command (building it is slow)."""
    return build_parser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CLI, description=TOP_HELP, formatter_class=Formatter, add_help=True
    )
    parser.add_argument("--run", default=None, help=argparse.SUPPRESS)
    parser.set_defaults(func=None)
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub(
        commands,
        "launch",
        "Start an isolated agent server from this checkout.",
        f"""\
  export {RUN_ENV}=$({CLI} launch --new --print-run)
  {CLI} launch --new --name baseline --checkout ../agent-sdk-base
  {CLI} launch --new --no-auth                    # exercise the unauthenticated mode
  {CLI} launch --new --deferred-init              # dormant until POST /api/init
  {CLI} launch --new --webhook-sink                # then: {CLI} sink read --path /events
  {CLI} launch --new --share-conversations-from $RUN_A   # a second instance on A's conversations
  {CLI} launch --new --config-json '{{"max_concurrent_runs": 1}}'

The server runs `python -m openhands.agent_server` with cwd, HOME,
OH_PERSISTENCE_DIR, config file, tmux dir, worktree root, session key and
secret key all private to the run. Without --new, an already-live run
(the one ${RUN_ENV} names, or the only one) is reused.""",
        cmd_launch,
    )
    p.add_argument("--new", action="store_true", help="always start a new run")
    p.add_argument(
        "--print-run", action="store_true", help="print only the run directory"
    )
    p.add_argument("--checkout", help="agent-sdk checkout to run (default: this one)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, help="port (default: a free one)")
    p.add_argument("--name", help="label appended to the run directory name")
    p.add_argument("--no-auth", action="store_true", help="no session API key")
    p.add_argument(
        "--no-secret-key",
        action="store_true",
        help="no OH_SECRET_KEY (with --no-auth: no cipher at all)",
    )
    p.add_argument(
        "--deferred-init", action="store_true", help="start dormant (OH_DEFERRED_INIT)"
    )
    p.add_argument(
        "--vscode",
        action="store_true",
        help="enable the VS Code service (off by default)",
    )
    p.add_argument(
        "--preload-tools", action="store_true", help="preload Chromium at startup"
    )
    p.add_argument("--config-json", help="extra agent-server config fields as JSON")
    p.add_argument(
        "--webhook-sink",
        action="store_true",
        help="start a recording webhook sink and point the server at it",
    )
    p.add_argument("--webhook-url", help="add a webhook base URL to the config")
    p.add_argument(
        "--canvas-ingress",
        action="store_true",
        help="set app_backend_public_url to this run's URL (Canvas App backend bridge)",
    )
    p.add_argument(
        "--share-conversations-from",
        metavar="RUN",
        help="use another run's conversations directory (lease and takeover checks)",
    )
    p.add_argument("--webhook-flush-delay", type=float, default=1.0)
    p.add_argument(
        "--env", action="append", metavar="K=V", help="extra server env (repeatable)"
    )
    p.add_argument(
        "--pass-env",
        action="append",
        metavar="NAME",
        help="forward one variable from this shell",
    )
    p.add_argument("--timeout", type=float, default=180)
    p.add_argument(
        "--force", action="store_true", help="launch even when memory is low"
    )

    p = sub(
        commands,
        "attach",
        "Drive an already-running agent server.",
        f"""\
  export {RUN_ENV}=$({CLI} attach --url http://127.0.0.1:8000 --key-env SESSION_API_KEY --print-run)
  {CLI} attach --url http://127.0.0.1:8000 --name docker-image""",
        cmd_attach,
    )
    p.add_argument("--url", required=True)
    p.add_argument("--key-env", help="env var holding the session API key")
    p.add_argument("--name")
    p.add_argument("--print-run", action="store_true")

    sub(
        commands,
        "doctor",
        "Read-only health check of the run.",
        f"""\
  {CLI} doctor
  {CLI} doctor --run $TMPDIR/agent-server-verify/<run>""",
        cmd_doctor,
    )

    p = sub(
        commands,
        "restart",
        "Restart the run's server with the same state.",
        f"""\
  {CLI} restart                    # persistence and reconnection checks
  {CLI} restart --rotate-key       # the old session key must now be rejected
  {CLI} restart --hard             # SIGKILL: running conversations come back as error
  {CLI} restart --rotate-secret-key  # new cipher key: are stored secrets still readable?
  {CLI} restart --env OH_MAX_CONCURRENT_RUNS=1""",
        cmd_restart,
    )
    p.add_argument("--rotate-key", action="store_true")
    p.add_argument(
        "--reset-config",
        action="store_true",
        help="drop earlier --config-json/--env overrides (launch flags too) before applying new ones",
    )
    p.add_argument(
        "--restore-secret-key",
        action="store_true",
        help="swap back to the secret key before the last --rotate-secret-key",
    )
    p.add_argument(
        "--rotate-secret-key",
        action="store_true",
        help="new OH_SECRET_KEY (cipher): what happens to secrets stored under the old one",
    )
    p.add_argument(
        "--hard",
        action="store_true",
        help="SIGKILL instead of a graceful stop (crash recovery)",
    )
    p.add_argument("--env", action="append", metavar="K=V")
    p.add_argument("--config-json")
    p.add_argument("--timeout", type=float, default=180)

    p = sub(
        commands,
        "stop",
        "Stop this run's server and helpers; evidence stays.",
        f"""\
  {CLI} stop
  {CLI} stop --purge-private       # also delete keys, state and fixtures""",
        cmd_stop,
    )
    p.add_argument("--purge-private", action="store_true")

    sub(commands, "runs", "List runs under the runs home.", f"  {CLI} runs", cmd_runs)

    p = sub(
        commands,
        "logs",
        "Tail or grep the server log.",
        f"""\
  {CLI} logs --tail 50
  {CLI} logs --grep 'ERROR|Traceback'""",
        cmd_logs,
    )
    p.add_argument("--tail", type=int, default=60)
    p.add_argument(
        "--expect-min", type=int, metavar="N", help="at least N matching lines"
    )
    p.add_argument("--expect-none", action="store_true", help="no matching lines")
    p.add_argument("--grep")

    p = sub(
        commands,
        "api",
        "Call any REST endpoint with the run's auth.",
        f"""\
  {CLI} api GET /server_info
  {CLI} api GET /api/conversations/search --query limit=5
  {CLI} api POST /api/conversations/<id>/pause --expect 200 --save F03.pause/pause
  {CLI} api PATCH /api/conversations/<id> --json '{{"title": "QA title"}}'
  {CLI} api GET /api/conversations/count --auth none --expect 401
  {CLI} api POST /api/file/upload --query path=/tmp/x/notes.txt --file file=./notes.txt
  {CLI} api GET /api/file/download --query path=/tmp/x/notes.txt --raw-out /tmp/notes.txt
  ID=$({CLI} api POST /api/conversations --json-file start.json --field id)
  {CLI} api GET /api/conversations/<id> --check execution_status eq finished --check title exists
  {CLI} api GET /server_info --auth none --check uptime lt "$U1" --check capabilities contains tool_catalog_v1
  {CLI} api GET /api/mcp/oauth/status/$JOB --check status eq callback_ready --until-ok 30
  {CLI} api GET /v1/models --auth bearer --check data len-ge 1
  {CLI} api OPTIONS /api/settings --auth none --header 'Origin: http://localhost:3000' \
      --header 'Access-Control-Request-Method: GET' --check-header access-control-allow-origin eq http://localhost:3000
  {CLI} api POST /api/auth/workspace-session --jar browser --check-header set-cookie contains HttpOnly
  {CLI} api GET "/api/conversations/$CID/workspace/index.html" --auth none --jar browser --expect 200
  CID=$({CLI} api POST /v1/chat/completions --auth bearer --json-file req.json --print-header X-OpenHands-ServerConversation-ID)
  {CLI} api POST /v1/chat/completions --auth bearer --json-file stream.json --sse --check frames.-1 eq '[DONE]'

--auth: header (default), none, bad (wrong key). --expect accepts 200, 2xx,
or a list like 404,422. --check FIELD OP [VALUE] asserts on the JSON body
(dotted paths; '.' is the whole body; numbers compare numerically; contains
works on lists and strings). --field prints one value instead of JSON, for
shell chaining. --save FEATURE/NAME stores the redacted exchange
under <run>/evidence/FEATURE/NAME.json.""",
        cmd_api,
    )
    p.add_argument(
        "method",
        type=str.upper,
        choices=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    p.add_argument("path")
    p.add_argument("--json", help="JSON request body")
    p.add_argument("--json-file", help="file with the JSON request body")
    p.add_argument("--stdin", action="store_true", help="read the JSON body from stdin")
    p.add_argument("--query", "-q", action="append", metavar="K=V")
    p.add_argument(
        "--form", action="append", metavar="K=V", help="multipart form field"
    )
    p.add_argument(
        "--file", action="append", metavar="FIELD=PATH", help="multipart file"
    )
    p.add_argument("--header", action="append", metavar="'Name: value'")
    p.add_argument("--cookie", help="raw Cookie header value")
    p.add_argument(
        "--auth",
        choices=["header", "none", "bad", "previous", "bearer", "bearer-bad", "init"],
        default="header",
    )
    p.add_argument("--expect", help="expected status: 200, 2xx, 404,422")
    p.add_argument(
        "--check",
        action="append",
        nargs="+",
        metavar="FIELD OP [VALUE]",
        help=f"assert on the JSON body (repeatable); OP: {', '.join(CHECK_OPS)}",
    )
    p.add_argument(
        "--until-ok",
        type=float,
        default=0,
        metavar="SECONDS",
        help="repeat the request every 0.5 s until --expect/--check hold (or time runs out)",
    )
    p.add_argument("--field", help="print only this dotted field of the JSON body")
    p.add_argument(
        "--print-header", metavar="NAME", help="print only this response header"
    )
    p.add_argument(
        "--sse",
        action="store_true",
        help="parse a text/event-stream body into {frames: [...]} for --check",
    )
    p.add_argument(
        "--check-header",
        action="append",
        nargs="+",
        metavar="NAME OP [VALUE]",
        help="assert on a response header (repeatable): set-cookie contains HttpOnly",
    )
    p.add_argument(
        "--all-headers", action="store_true", help="show every response header"
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="omit request and response bodies from the output (checks still see them)",
    )
    p.add_argument(
        "--expect-max-ms",
        type=int,
        metavar="MS",
        help="fail when the call takes longer",
    )
    p.add_argument(
        "--jar",
        metavar="NAME",
        help="browser-like cookie jar under the run: send matching cookies, store Set-Cookie",
    )
    p.add_argument("--save", metavar="FEATURE/NAME")
    p.add_argument(
        "--raw-out", metavar="PATH", help="write the raw response body to PATH"
    )
    p.add_argument("--max-chars", type=int, default=6000)
    p.add_argument("--timeout", type=float, default=60)

    ws = commands.add_parser(
        "ws",
        help="WebSocket capture",
        description="WebSocket capture: listen | start | read | stop",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} ws listen /sockets/events/<id> --query resend_mode=all --duration 5 --save F04.ws-replay/replay
  {CLI} ws start /sockets/events/<id> --name live
  {CLI} conversation send <id> --text 'say hi'
  {CLI} ws stop live --expect-kind MessageEvent
  {CLI} ws listen /sockets/bash-events --auth bad --expect-close 4001
  {CLI} ws listen /sockets/bash-events --auth query --query session_api_key=wrong --expect-reject 403
  {CLI} ws listen /app-backends/qa-backend/ws --auth none --header 'Origin: http://127.0.0.1:8000' \
      --header "Cookie: oh_app_backend_session=$TOKEN" --send hello --count 1""",
    )
    add_run_flag(ws)
    ws_sub = ws.add_subparsers(dest="ws_command", metavar="<listen|start|read|stop>")
    wl = ws_sub.add_parser(
        "listen",
        help="connect, optionally send frames, capture until a condition",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} ws listen /sockets/events/<id> --query resend_mode=all --duration 5
  {CLI} ws listen /sockets/events/<id> --until-kind ConversationStateUpdateEvent --duration 30
  {CLI} ws listen /sockets/bash-events --auth bad --expect-close 4001
  {CLI} ws listen /sockets/events/<id> --send '{{"role":"user","content":[{{"type":"text","text":"hi"}}],"run":true}}' --duration 20

--auth: first-frame (default; sends {{"type":"auth",...}} first), query
(deprecated session_api_key param), header, none, bad.""",
    )
    add_run_flag(wl)
    wl.set_defaults(func=cmd_ws_listen)
    wl.add_argument("path")
    wl.add_argument(
        "--auth",
        choices=WS_AUTH_MODES,
        default="first-frame",
    )
    wl.add_argument("--query", "-q", action="append", metavar="K=V")
    wl.add_argument(
        "--header",
        action="append",
        metavar="'Name: value'",
        help="extra handshake header",
    )
    wl.add_argument(
        "--send",
        action="append",
        metavar="JSON",
        help="frame to send after auth (repeatable)",
    )
    wl.add_argument("--key", help="with --auth key: authenticate with this exact key")
    wl.add_argument(
        "--expect-kind",
        action="append",
        metavar="KIND",
        help="this kind must arrive (repeatable)",
    )
    wl.add_argument(
        "--expect-no-kind",
        action="append",
        metavar="KIND",
        help="this kind must not arrive",
    )
    wl.add_argument("--expect-frames", type=int, metavar="N", help="exactly N frames")
    wl.add_argument("--duration", type=float, default=10)
    wl.add_argument("--until-kind")
    wl.add_argument("--until", action="append", metavar="FIELD=VALUE")
    wl.add_argument("--count", type=int)
    wl.add_argument(
        "--expect-close", type=int, help="pass only if the server closes with this code"
    )
    wl.add_argument(
        "--expect-open", action="store_true", help="pass only if the socket stayed open"
    )
    wl.add_argument(
        "--expect-reject",
        type=int,
        metavar="STATUS",
        help="pass only if the handshake is refused with this HTTP status (e.g. 403)",
    )
    wl.add_argument("--out", help=argparse.SUPPRESS)
    wl.add_argument("--save", metavar="FEATURE/NAME")
    wl.add_argument(
        "--show", type=int, default=8, help="frames to include in the output"
    )

    wst = ws_sub.add_parser(
        "start",
        help="record a socket in the background",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} ws start /sockets/events/<id> --name live --duration 300",
    )
    add_run_flag(wst)
    wst.set_defaults(func=cmd_ws_start)
    wst.add_argument("path")
    wst.add_argument("--name")
    wst.add_argument(
        "--auth",
        choices=WS_AUTH_MODES,
        default="first-frame",
    )
    wst.add_argument("--query", "-q", action="append", metavar="K=V")
    wst.add_argument("--send", action="append", metavar="JSON")
    wst.add_argument("--header", action="append", metavar="'Name: value'")
    wst.add_argument("--key", help="with --auth key: authenticate with this exact key")
    wst.add_argument("--duration", type=float, default=600)
    wst.add_argument(
        "--settle", type=float, default=1.0, help="seconds to wait after connecting"
    )

    for name, func in (("read", cmd_ws_read), ("stop", cmd_ws_stop)):
        wr = ws_sub.add_parser(
            name,
            help=f"{name} a background capture",
            formatter_class=Formatter,
            epilog=f"Examples:\n  {CLI} ws {name} live --kinds MessageEvent --show 3\n"
            f"  {CLI} ws {name} live --contains QA_MARKER --save F04.ws-live/frames",
        )
        add_run_flag(wr)
        wr.set_defaults(func=func)
        wr.add_argument("name")
        wr.add_argument("--kinds", help="comma-separated kinds to show")
        wr.add_argument("--contains", help="only frames whose JSON contains this text")
        wr.add_argument("--expect-kind", help="pass only if this kind was received")
        wr.add_argument(
            "--expect-min",
            type=int,
            metavar="N",
            help="pass only with at least N frames matching --kinds/--contains",
        )
        wr.add_argument(
            "--expect-none",
            action="store_true",
            help="pass only if no frame matches --kinds/--contains",
        )
        wr.add_argument(
            "--expect-close",
            type=int,
            metavar="CODE",
            help="pass only if the socket closed with this code",
        )
        wr.add_argument(
            "--expect-open",
            action="store_true",
            help="pass only if the socket was still open",
        )
        wr.add_argument(
            "--wait",
            type=float,
            default=0,
            metavar="SECONDS",
            help="poll up to SECONDS until the expectations hold",
        )
        wr.add_argument("--show", type=int, default=5)
        wr.add_argument("--save", metavar="FEATURE/NAME")

    llm = commands.add_parser(
        "llm",
        help="LLM profiles from an env key",
        description="LLM profiles: preset | set | show",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} llm preset deepseek                 # deepseek-flash (active) + deepseek-pro from $DEEPSEEK_API_KEY
  {CLI} llm set --profile alt --model deepseek/deepseek-v4-pro --api-key-env DEEPSEEK_API_KEY --no-activate
  {CLI} llm show""",
    )
    add_run_flag(llm)
    llm_sub = llm.add_subparsers(dest="llm_command", metavar="<preset|set|show>")
    lp = llm_sub.add_parser(
        "preset",
        help="save a known set of profiles",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} llm preset deepseek\n  {CLI} llm preset deepseek --api-key-file ~/.deepseek",
    )
    add_run_flag(lp)
    lp.set_defaults(func=cmd_llm_preset)
    lp.add_argument("preset", choices=sorted(PRESETS))
    lp.add_argument("--api-key-env")
    lp.add_argument("--api-key-file")
    lp.add_argument(
        "--no-validate", action="store_true", help="skip the one-call validation"
    )
    ls = llm_sub.add_parser(
        "set",
        help="save one profile",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} llm set --profile qa --model deepseek/deepseek-flash --api-key-env DEEPSEEK_API_KEY",
    )
    add_run_flag(ls)
    ls.set_defaults(func=cmd_llm_set)
    ls.add_argument("--profile", required=True)
    ls.add_argument("--model", required=True)
    ls.add_argument("--base-url")
    ls.add_argument("--api-key-env")
    ls.add_argument("--api-key-file")
    ls.add_argument(
        "--no-api-key", action="store_true", help="placeholder key (llm-stub)"
    )
    ls.add_argument(
        "--num-retries",
        type=int,
        help="LLM num_retries (0 makes error stubs fail fast)",
    )
    ls.add_argument("--llm-timeout", type=float, help="LLM request timeout in seconds")
    ls.add_argument(
        "--extra-json", help="more LLM fields as JSON, e.g. '{\"temperature\": 0}'"
    )
    ls.add_argument("--no-activate", action="store_true")
    ls.add_argument("--no-validate", action="store_true")
    lw = llm_sub.add_parser(
        "show",
        help="list saved profiles",
        epilog=f"Examples:\n  {CLI} llm show",
        formatter_class=Formatter,
    )
    add_run_flag(lw)
    lw.set_defaults(func=cmd_llm_show)

    conv = commands.add_parser(
        "conversation",
        help="essential conversation pathways",
        description="Conversations: start | wait | send | events",
        formatter_class=Formatter,
        epilog=f"""Examples:
  CID=$({CLI} conversation start --prompt 'Create hello.txt containing hi' --wait --print-id)
  {CLI} conversation start --workspace $({CLI} fixture git-repo --print-path) --no-run
  {CLI} conversation send "$CID" --text 'Now delete it' --wait
  {CLI} conversation wait "$CID" --until idle,finished --timeout 300
  {CLI} conversation events "$CID" --kinds ObservationEvent --contains hello.txt""",
    )
    add_run_flag(conv)
    conv_sub = conv.add_subparsers(
        dest="conversation_command", metavar="<start|wait|send|events>"
    )
    cs = conv_sub.add_parser(
        "start",
        help="POST /api/conversations from the saved settings",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} conversation start --prompt 'Say hi' --wait --print-id
  {CLI} conversation start --agent-profile default --workspace /tmp/w --no-run
  {CLI} conversation start --body-json '{{"max_iterations": 5}}' --prompt 'List files' --wait
  {CLI} conversation start --tools none --no-autotitle --prompt 'Say hi' --wait     # no tmux/browser needed
  {CLI} conversation start --confirmation-policy always --prompt 'Run: ls' --wait --until waiting_for_confirmation

Without --body-json agent fields, the agent comes from GET /api/settings
(X-Expose-Secrets: encrypted) as agent_settings, like Agent Canvas does.""",
    )
    add_run_flag(cs)
    cs.set_defaults(func=cmd_conversation_start)
    cs.add_argument("--prompt")
    cs.add_argument(
        "--no-run", action="store_true", help="queue the prompt without running"
    )
    cs.add_argument("--workspace", help="working directory for the conversation")
    cs.add_argument("--agent-profile", help="agent profile name or id")
    cs.add_argument("--body-json", help="extra StartConversationRequest fields")
    cs.add_argument("--title")
    cs.add_argument("--tag", action="append", metavar="K=V")
    cs.add_argument("--max-iterations", type=int)
    cs.add_argument(
        "--llm-json",
        help="merge these fields into agent_settings.llm, e.g. '{\"stream\": true}'",
    )
    cs.add_argument(
        "--placeholder-agent",
        action="store_true",
        help="an agent whose model is never reachable: a workspace without an LLM profile",
    )
    cs.add_argument(
        "--tools", help="comma-separated tool names for agent_settings, or 'none'"
    )
    cs.add_argument("--no-autotitle", action="store_true")
    cs.add_argument(
        "--secret",
        action="append",
        metavar="NAME=VALUE",
        help="conversation secret (StaticSecret); the value is redacted in output",
    )
    cs.add_argument("--confirmation-policy", choices=sorted(CONFIRMATION_POLICIES))
    cs.add_argument("--wait", action="store_true", help="wait for --until statuses")
    cs.add_argument(
        "--until", default="idle,finished,error,stuck,paused,waiting_for_confirmation"
    )
    cs.add_argument("--timeout", type=float, default=300)
    cs.add_argument("--print-id", action="store_true")
    cs.add_argument("--save", metavar="FEATURE/NAME")
    cw = conv_sub.add_parser(
        "wait",
        help="poll until the execution status matches",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} conversation wait <id> --until finished --timeout 300",
    )
    add_run_flag(cw)
    cw.set_defaults(func=cmd_conversation_wait)
    cw.add_argument("id")
    cw.add_argument(
        "--until", default="idle,finished,error,stuck,paused,waiting_for_confirmation"
    )
    cw.add_argument("--timeout", type=float, default=300)
    cm = conv_sub.add_parser(
        "send",
        help="POST a user message",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} conversation send <id> --text 'continue' --wait",
    )
    add_run_flag(cm)
    cm.set_defaults(func=cmd_conversation_send)
    cm.add_argument("id")
    cm.add_argument("--text", required=True)
    cm.add_argument("--no-run", action="store_true")
    cm.add_argument("--wait", action="store_true")
    cm.add_argument(
        "--until", default="idle,finished,error,stuck,paused,waiting_for_confirmation"
    )
    cm.add_argument("--timeout", type=float, default=300)
    ce = conv_sub.add_parser(
        "events",
        help="page through events and summarize",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} conversation events <id>
  {CLI} conversation events <id> --kinds ObservationEvent --contains hello.txt --expect-kind ObservationEvent
  {CLI} conversation events <id> --kinds MessageEvent --show 2 --full --save F04.events/messages""",
    )
    add_run_flag(ce)
    ce.set_defaults(func=cmd_conversation_events)
    ce.add_argument("id")
    ce.add_argument("--kinds")
    ce.add_argument("--contains")
    ce.add_argument(
        "--key",
        help="only ConversationStateUpdateEvent rows with this key (goal, execution_status, ...)",
    )
    ce.add_argument("--expect-kind", help="pass only if a selected event has this kind")
    ce.add_argument(
        "--expect-count", type=int, metavar="N", help="exactly N selected events"
    )
    ce.add_argument(
        "--expect-min", type=int, metavar="N", help="at least N selected events"
    )
    ce.add_argument(
        "--show", type=int, default=10, help="show the last N matching events"
    )
    ce.add_argument(
        "--full", action="store_true", help="show full events instead of briefs"
    )
    ce.add_argument("--save", metavar="FEATURE/NAME")

    p = sub(
        commands,
        "fixture",
        "Create run-owned fixtures.",
        f"""\
  REPO=$({CLI} fixture git-repo --print-path)       # 2 commits, 1 modified, 1 untracked
  {CLI} fixture skill --name qa-skill
  {CLI} fixture mcp-server --name qa-mcp            # stdio FastMCP server with a qa_echo tool
  {CLI} fixture plugin --name qa-plugin             # .plugin/plugin.json, a command, a skill, hooks
  {CLI} fixture git-source --name qa-src            # git repo with skills/qa-src-skill and plugins/qa-src-plugin; file:// url
  {CLI} fixture git-source --name qa-src            # again: commits a change (refresh tests)
  {CLI} fixture marketplace --name qa-market        # .plugin/marketplace.json listing a plugin and a skill
  {CLI} fixture project --name qa-project           # git project: .agents/skills, .openhands/skills, AGENTS.md, .agents/agents, hooks.json
  {CLI} fixture canvas-app --name qa-canvas         # canvas-extension.json, dist/index.js, assets/icon.svg (git repo)
  {CLI} fixture canvas-app --name qa-backend --backend ok   # plus backend/linux.tar.gz with its sha256 in the manifest
  {CLI} fixture http-sink --name telemetry          # records every request; `sink read --name telemetry`
  {CLI} fixture http-sink --name creds --body '{{"token": "qa"}}' --status 200
  {CLI} fixture llm-stub --name qa-stub --step 'tool:finish:{{"message":"done"}}'
  {CLI} fixture llm-stub --name qa-stub --step status:500      # rewrites the script of a running stub
  {CLI} fixture list

llm-stub is an OpenAI-compatible provider on 127.0.0.1 that answers each
chat completion with the next scripted step (the last step repeats). Use it
only for provider behavior a real model cannot produce on demand (errors,
hangs, an exact tool call); happy paths use a real model. Its requests are
readable with `sink read --name <stub>`.""",
        cmd_fixture,
    )
    p.add_argument(
        "kind",
        choices=[*sorted(FIXTURES), "canvas-app", "http-sink", "llm-stub", "list"],
    )
    p.add_argument("--name")
    p.add_argument("--print-path", action="store_true")
    p.add_argument(
        "--backend",
        choices=["ok", "unhealthy", "exit"],
        help="canvas-app: add a backend artifact (aiohttp echo + /health + /ws echo)",
    )
    p.add_argument("--status", type=int, default=200, help="http-sink reply status")
    p.add_argument("--body", help="http-sink reply body (JSON text)")
    p.add_argument(
        "--step",
        action="append",
        metavar="STEP",
        help="llm-stub script step: reply:TEXT[@usage=P,C] | tool:NAME:JSON | status:CODE | hang:SECONDS",
    )

    sk = commands.add_parser(
        "sink",
        help="read what an HTTP sink (webhooks, telemetry, credential source) received",
        description="HTTP sink: read",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} sink read --path /events --expect-min 1                 # the --webhook-sink of launch
  {CLI} sink read --name telemetry --contains server_started
  {CLI} sink read --path /conversations --contains finished --save F30.deliver/conversations""",
    )
    add_run_flag(sk)
    sk_sub = sk.add_subparsers(dest="sink_command", metavar="<read>")
    skr = sk_sub.add_parser(
        "read",
        help="summarize recorded requests",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} sink read --path /events --show 2\n"
        f"  {CLI} sink read --path /conversations --contains finished --expect-min 1 --wait 30",
    )
    add_run_flag(skr)
    skr.set_defaults(func=cmd_sink_read)
    skr.add_argument("--name", default="webhooks", help="sink name (default: webhooks)")
    skr.add_argument("--path", help="only requests whose path starts with this")
    skr.add_argument("--contains")
    skr.add_argument(
        "--expect-min", type=int, help="pass only with at least N matching requests"
    )
    skr.add_argument(
        "--expect-max",
        type=int,
        help="pass only with at most N matching requests (0: none)",
    )
    skr.add_argument(
        "--wait",
        type=float,
        default=0,
        metavar="SECONDS",
        help="poll up to SECONDS until --expect-min matching requests arrived",
    )
    skr.add_argument("--show", type=int, default=3)
    skr.add_argument("--save", metavar="FEATURE/NAME")

    st = commands.add_parser(
        "state",
        help="inspect files the server persisted for this run",
        description="Run state on disk: ls | cat | grep",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} state ls 'home/.openhands/*'
  {CLI} state cat home/.openhands/secrets.json --check . exists
  {CLI} state ls 'server/workspace/conversations/*/meta.json'
  {CLI} state cat meta.json --conversation "$CID" --check title eq 'QA title'

Paths are relative to the run directory: home/ (HOME and
home/.openhands = OH_PERSISTENCE_DIR), server/workspace/ (conversations,
bash_events, default project), fixtures/, worktrees/. Output is redacted.""",
    )
    add_run_flag(st)
    st_sub = st.add_subparsers(dest="state_command", metavar="<ls|cat|grep>")
    stl = st_sub.add_parser(
        "ls",
        help="list files matching a glob",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} state ls 'home/.openhands/**/*.json'",
    )
    add_run_flag(stl)
    stl.set_defaults(func=cmd_state_ls)
    stl.add_argument("pattern", nargs="?", default="home/.openhands/*")
    stl.add_argument(
        "--expect-count", type=int, metavar="N", help="exactly N matching files"
    )
    stl.add_argument(
        "--expect-min", type=int, metavar="N", help="at least N matching files"
    )
    stl.add_argument(
        "--conversation", metavar="ID", help="relative to this conversation's directory"
    )
    stg = st_sub.add_parser(
        "grep",
        help="find files under the run that contain a value (secrets at rest)",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} state grep --env-value QA_SECRET --glob 'home/**/*' --expect-none     # never stored in plaintext
  {CLI} state grep qa-marker --glob 'server/workspace/conversations/**/*'

The searched value is redacted from the output; private/ and evidence/ are
never searched.""",
    )
    add_run_flag(stg)
    stg.set_defaults(func=cmd_state_grep)
    stg.add_argument("text", nargs="?")
    stg.add_argument(
        "--env-value", metavar="NAME", help="search for the value of this variable"
    )
    stg.add_argument("--glob", default="**/*")
    stg.add_argument(
        "--expect-none", action="store_true", help="pass only when nothing matches"
    )
    stc = st_sub.add_parser(
        "cat",
        help="print one file (JSON parsed when possible)",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} state cat home/.openhands/settings.json --check agent_settings.llm.model eq deepseek/deepseek-flash",
    )
    add_run_flag(stc)
    stc.set_defaults(func=cmd_state_cat)
    stc.add_argument("path")
    stc.add_argument(
        "--conversation", metavar="ID", help="relative to this conversation's directory"
    )
    stc.add_argument("--check", action="append", nargs="+", metavar="FIELD OP [VALUE]")
    stc.add_argument("--contains", help="pass only if the raw file contains this text")
    stc.add_argument("--not-contains", help="pass only if the raw file lacks this text")
    stc.add_argument("--max-chars", type=int, default=6000)
    stc.add_argument("--mode", help="expected octal permissions, e.g. 600")

    sub(
        commands,
        "capabilities",
        "What this machine can drive: binaries, keys, and which families are blocked.",
        f"""\
  {CLI} capabilities

Families declare their prerequisites on a `Needs:` line ({", ".join(NEEDS)});
`map run --all --fresh` reports families with unmet needs as blocked instead
of running them.""",
        cmd_capabilities,
    )
    sub(
        commands,
        "config",
        "Show the run's server config file and environment (secrets masked).",
        f"""\
  {CLI} config""",
        cmd_config,
    )

    p = sub(
        commands,
        "exec",
        "Run a program against the run with AGENT_SERVER_URL/SESSION_API_KEY set.",
        f"""\
  {CLI} exec --save F20.example-01/run -- uv run python examples/02_remote_agent_server/01_convo_with_local_agent_server.py
  {CLI} exec --env LLM_MODEL=deepseek/deepseek-flash --expect-output OK -- uv run python my_probe.py""",
        cmd_exec,
    )
    p.add_argument("--env", action="append", metavar="K=V")
    p.add_argument("--cwd")
    p.add_argument(
        "--openai",
        action="store_true",
        help="also export OPENAI_BASE_URL=<run>/v1 and OPENAI_API_KEY=<run key>",
    )
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument(
        "--expect-output",
        action="append",
        help="stdout/stderr must contain this (repeatable)",
    )
    p.add_argument(
        "--reject-output", action="append", help="stdout/stderr must not contain this"
    )
    p.add_argument("--tail-chars", type=int, default=1500)
    p.add_argument("--save", metavar="FEATURE/NAME")
    p.add_argument("command", nargs=argparse.REMAINDER)

    ev = commands.add_parser(
        "evidence",
        help="the evidence ledger",
        description="Evidence: add | report",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} evidence add --feature F03.create --result pass --entry 'POST /api/conversations' \\
      --expected 'status 201 and id' --actual '201, id returned' --artifact evidence/F03.create/start.json
  {CLI} evidence report --out report.md""",
    )
    add_run_flag(ev)
    ev_sub = ev.add_subparsers(dest="evidence_command", metavar="<add|report>")
    ea = ev_sub.add_parser(
        "add",
        help="append a ledger row",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} evidence add --feature F01.alive --result pass --entry 'GET /alive' --expected 200 --actual 200",
    )
    add_run_flag(ea)
    ea.set_defaults(func=cmd_evidence_add)
    ea.add_argument("--feature", required=True)
    ea.add_argument("--result", required=True, choices=RESULTS)
    ea.add_argument("--entry", required=True, help="entry point exercised")
    ea.add_argument("--expected", required=True)
    ea.add_argument("--actual", required=True)
    ea.add_argument("--artifact", action="append")
    ea.add_argument("--note")
    ea.add_argument(
        "--allow-unknown", action="store_true", help="accept an ID not yet in the map"
    )
    er = ev_sub.add_parser(
        "report",
        help="latest result per sub-feature, fail/blocked first",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} evidence report --out report.md --print-markdown\n"
        f"  {CLI} evidence report --runs <runA> <runB>",
    )
    add_run_flag(er)
    er.set_defaults(func=cmd_evidence_report)
    er.add_argument("--runs", nargs="+", help="merge the ledgers of several runs")
    er.add_argument("--out")
    er.add_argument("--print-markdown", action="store_true")

    mp = commands.add_parser(
        "map",
        help="feature map tooling",
        description="Map: run | check | coverage | ids | routes | diff | owners",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} map run --file F01 --record         # execute a family's recipes and record the ledger
  {CLI} map check                           # structure, IDs, index counts, commands parse, routes exist
  {CLI} map check --file F03 --no-routes
  {CLI} map coverage                        # routes no family owns or drives
  {CLI} map routes --module conversation_router
  {CLI} map diff --base ../agent-sdk-base       # routes added/removed/deprecated since BASE
  {CLI} map owners --changed origin/main...HEAD""",
    )
    mp_sub = mp.add_subparsers(
        dest="map_command", metavar="<run|check|coverage|ids|routes|diff|owners>"
    )
    mc = mp_sub.add_parser(
        "check",
        help="validate the map",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} map check\n  {CLI} map check --file F03\n  {CLI} map check --fix-counts",
    )
    mc.set_defaults(func=cmd_map_check)
    mc.add_argument("--file")
    mc.add_argument("--fix-counts", action="store_true")
    mc.add_argument(
        "--no-routes",
        action="store_true",
        help="skip importing the app for route checks",
    )
    mv = mp_sub.add_parser(
        "coverage",
        help="routes no family owns or drives",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} map coverage",
    )
    mv.set_defaults(func=cmd_map_coverage)
    mv.add_argument("--checkout")
    mrun = mp_sub.add_parser(
        "run",
        help="execute a family's fenced sh blocks top to bottom in one run",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} map run --file F01
  {CLI} map run --file F04 --record            # also append ledger rows per bullet
  {CLI} map run --file F04 --only F04.pause    # Preconditions + the named bullets
  {CLI} map run --file F04 --dry-run           # print the scripts without running
  {CLI} map run --file F03 --fresh --record    # own run with the family's Launch: flags
  {CLI} map run --all --fresh --record         # the whole map, one fresh run per family
  {CLI} map run --file F08 --only F08.x --fresh --repeat 2 --checkout ../base   # baseline of a fix

Each bullet's ```sh blocks run in `bash -c` with `set -eo pipefail`, from the
repository root, with this run exported. Shell variables assigned in one
bullet (CID=..., export X=...) are carried into later bullets. A bullet fails
when any command exits non-zero, so recipes encode their expectations with
--expect, --check, --expect-kind and `test`. A bullet whose label says
"known bug" is an expected failure (xfail; an unexpected pass, xpass, fails
the run so the map gets updated). A bullet whose label says "blocked" is
reported and not executed. Transcripts are saved under <run>/evidence/map-run/.""",
    )
    add_run_flag(mrun)
    mrun.set_defaults(func=cmd_map_run)
    mrun.add_argument("--file")
    mrun.add_argument(
        "--fresh",
        action="store_true",
        help="launch a new run with the family's `Launch:` flags, replay, stop it",
    )
    mrun.add_argument("--all", action="store_true", help="every family (needs --fresh)")
    mrun.add_argument(
        "--checkout", help="with --fresh: launch this checkout (a baseline worktree)"
    )
    mrun.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="with --fresh: replay N times, each on its own fresh run",
    )
    mrun.add_argument("--only", help="comma-separated sub-feature IDs")
    mrun.add_argument(
        "--keep-going", action="store_true", help="run later bullets after a failure"
    )
    mrun.add_argument(
        "--record", action="store_true", help="append a ledger row per ID"
    )
    mrun.add_argument("--dry-run", action="store_true")
    mrun.add_argument("--timeout", type=float, default=900, help="seconds per bullet")
    mi = mp_sub.add_parser(
        "ids",
        help="every sub-feature ID",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} map ids --file F03",
    )
    mi.set_defaults(func=cmd_map_ids)
    mi.add_argument("--file")
    mr = mp_sub.add_parser(
        "routes",
        help="every HTTP and WebSocket route of the app",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} map routes --module bash_router",
    )
    mr.set_defaults(func=cmd_map_routes)
    mr.add_argument("--module")
    mr.add_argument("--checkout")
    md = mp_sub.add_parser(
        "diff",
        help="routes added, removed or newly deprecated between two checkouts",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} map diff --base ../agent-sdk-base\n"
        f"  {CLI} map diff --base ../base-worktree --target ../target-worktree",
    )
    md.set_defaults(func=cmd_map_diff)
    md.add_argument("--base", required=True, help="BASE checkout (a worktree)")
    md.add_argument("--target", help="TARGET checkout (default: this one)")
    mo = mp_sub.add_parser(
        "owners",
        help="map changed source paths to families",
        formatter_class=Formatter,
        epilog=f"Examples:\n  {CLI} map owners --changed $BASE..$TARGET\n"
        f"  {CLI} map owners openhands-agent-server/openhands/agent_server/bash_router.py",
    )
    mo.set_defaults(func=cmd_map_owners)
    mo.add_argument("paths", nargs="*")
    mo.add_argument("--changed", metavar="BASE..TARGET")
    return parser


def extract_run_flag(argv: list[str]) -> tuple[list[str], str | None]:
    """--run DIR may appear anywhere on the line."""
    out, run = [], None
    skip = False
    for i, token in enumerate(argv):
        if skip:
            skip = False
            continue
        if token == "--run" and i + 1 < len(argv):
            run = argv[i + 1]
            skip = True
        elif token.startswith("--run="):
            run = token.split("=", 1)[1]
        else:
            out.append(token)
    return out, run


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--" in raw:
        split = raw.index("--")
        head, tail = raw[:split], raw[split:]
    else:
        head, tail = raw, []
    head, run_flag = extract_run_flag(head)
    if head[:1] == ["_http-sink"]:
        serve_http_sink(int(head[1]), Path(head[2]))
        return 0
    if head[:1] == ["_llm-stub"]:
        serve_llm_stub(int(head[1]), Path(head[2]), Path(head[3]))
        return 0
    parser = build_parser()
    if not head:
        parser.print_help()
        return 0
    args = parser.parse_args(head + tail)
    args.run = run_flag
    func = args.func
    if func is None:
        # A group like `ws` or `map` without a subcommand: show its help.
        parser.parse_args([*head, "--help"])
        return 2
    try:
        result = func(args)
    except CliError as exc:
        payload: dict[str, Any] = {
            "ok": False,
            "exit": exc.exit_code,
            "error": str(exc),
        }
        if exc.hint:
            payload["hint"] = exc.hint
        if exc.example:
            payload["example"] = exc.example
        payload.update(exc.data)
        emit(payload)
        return exc.exit_code
    except json.JSONDecodeError as exc:
        emit({"ok": False, "exit": 2, "error": f"invalid JSON: {exc}"})
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if type(exc).__module__.startswith("httpx"):
            emit(
                {
                    "ok": False,
                    "exit": 3,
                    "error": f"{type(exc).__name__}: {exc}",
                    "hint": f"Is the server up? `{CLI} doctor`",
                }
            )
            return 3
        raise
    emit(result)
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
