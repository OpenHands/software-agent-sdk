"""Guard: the agent server maps errors to HTTP in one place.

Routers, services and stores raise ``openhands.sdk.errors`` categories, and
``openhands.agent_server.errors.ERROR_STATUS`` maps them to statuses. A hand-
built ``HTTPException`` anywhere else would be a second mapping, which is how
routes drifted before (a route copied without its error mapping answered 500).
"""

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_SERVER = REPO_ROOT / "openhands-agent-server" / "openhands" / "agent_server"

# api.py renders errors: it handles FastAPI's own HTTPException (routing,
# security dependencies) and builds one to render a domain error.
ALLOWED = {AGENT_SERVER / "api.py"}


def _http_exception_uses(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "HTTPException":
            lines.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "HTTPException":
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "HTTPException" for alias in node.names):
                lines.append(node.lineno)
    return lines


def test_agent_server_never_builds_http_exception_outside_api() -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{line}"
        for path in sorted(AGENT_SERVER.rglob("*.py"))
        if path not in ALLOWED
        for line in _http_exception_uses(path)
    ]
    assert not offenders, (
        "Raise an openhands.sdk.errors category instead of HTTPException; "
        "openhands.agent_server.errors.ERROR_STATUS maps it to a status:\n"
        + "\n".join(offenders)
    )


def test_guard_detects_http_exception(tmp_path: Path) -> None:
    sample = tmp_path / "router.py"
    sample.write_text(
        "from fastapi import HTTPException\n"
        "def f():\n"
        "    raise HTTPException(404, 'x')\n"
    )
    assert _http_exception_uses(sample) == [1, 3]
