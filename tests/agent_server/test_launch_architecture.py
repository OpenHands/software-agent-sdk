"""Only the launch pipeline builds or changes a launch agent in the agent-server."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import openhands.agent_server


PACKAGE_ROOT = Path(openhands.agent_server.__file__).parent
LAUNCH_MODULE = PACKAGE_ROOT / "launch.py"
AGENT_CLASSES = {"Agent", "ACPAgent"}
AGENT_SOURCES = {"agent", "agent_settings", "agent_profile_id"}
ALLOWED: set[tuple[str, int]] = set()


def _name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_request(node: ast.expr) -> bool:
    return "request" in ast.unparse(node).lower()


def _updates_agent(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg == "update" and isinstance(keyword.value, ast.Dict):
            keys = keyword.value.keys
            if any(isinstance(k, ast.Constant) and k.value == "agent" for k in keys):
                return True
    return False


def _violations(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _name(node.func)
            if name in {"create_agent", "create_agent_from_settings"}:
                found.append((node.lineno, f"{name}() builds an agent"))
            elif name in AGENT_CLASSES:
                found.append((node.lineno, f"{name}(...) constructs an agent"))
            elif name == "LaunchedAgent":
                found.append((node.lineno, "LaunchedAgent is built by finalize()"))
            elif name == "create_request" and not any(
                keyword.arg in AGENT_SOURCES for keyword in node.keywords
            ):
                found.append(
                    (node.lineno, "create_request() without a source builds an agent")
                )
            elif (
                name == "model_copy"
                and isinstance(node.func, ast.Attribute)
                and _is_request(node.func.value)
                and _updates_agent(node)
            ):
                found.append((node.lineno, "a start request's agent is replaced"))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "agent"
                    and _is_request(target.value)
                ):
                    found.append((node.lineno, "a start request's agent is assigned"))
    return found


def _modules() -> list[Path]:
    return sorted(path for path in PACKAGE_ROOT.rglob("*.py") if path != LAUNCH_MODULE)


def test_no_module_outside_the_launch_pipeline_builds_a_launch_agent():
    violations = [
        f"{path.relative_to(PACKAGE_ROOT)}:{line}: {reason}"
        for path in _modules()
        for line, reason in _violations(ast.parse(path.read_text()))
        if (str(path.relative_to(PACKAGE_ROOT)), line) not in ALLOWED
    ]
    assert violations == []


@pytest.mark.parametrize(
    "source",
    [
        "agent = settings.create_agent()",
        "agent = Agent(llm=llm, tools=[])",
        "agent = ACPAgent(acp_command=['x'])",
        "request = settings.create_request(StartConversationRequest, workspace=w)",
        "request = request.model_copy(update={'agent': agent})",
        "request.agent = agent",
        "launched = LaunchedAgent(agent=a, profile=None)",
    ],
)
def test_the_check_catches_each_way_of_building_an_agent(source):
    assert _violations(ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "isinstance(agent, ACPAgent)",
        "request = settings.create_request(Req, agent_profile_id=pid)",
        "info = info.model_copy(update={'agent': trimmed})",
        "state.agent = agent",
    ],
)
def test_the_check_allows_uses_that_do_not_build_a_launch_agent(source):
    assert _violations(ast.parse(source)) == []
