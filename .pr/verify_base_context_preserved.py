"""End-to-end verification that `base_context` survives
`load_skills_from_agent_server()` on both RemoteWorkspace and its
OpenHandsCloudWorkspace override.

This exercises real AgentContext construction and real Pydantic
model_copy behavior. The only thing mocked is the outbound HTTP call to
the agent-server's /api/skills endpoint (_call_skills_api), since no live
sandbox is available in this environment -- RemoteWorkspace,
OpenHandsCloudWorkspace, AgentContext, and model_copy are all real,
unmocked SDK code.

Run with:
    uv run python .pr/verify_base_context_preserved.py
"""

from datetime import UTC, datetime
from unittest.mock import patch

from openhands.sdk import RemoteWorkspace
from openhands.sdk.context import AgentContext
from openhands.workspace import OpenHandsCloudWorkspace


FIXED_TIME = datetime(2020, 1, 1, tzinfo=UTC)

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    results.append(condition)


# --- Part A: RemoteWorkspace, skills found, base_context provided ---
workspace = RemoteWorkspace(
    host="https://agent-server.example.com", working_dir="/workspace"
)
base_context = AgentContext(
    marketplace_path="internal/marketplace.json",
    disabled_skills=["risky-skill"],
    user_message_suffix="Follow the client's policy.",
    current_datetime=FIXED_TIME,
)

with patch.object(
    workspace,
    "_call_skills_api",
    return_value=[{"name": "demo-skill", "content": "demo"}],
):
    skills, context = workspace.load_skills_from_agent_server(base_context=base_context)

check("A: skills loaded", len(skills) == 1)
check(
    "A: marketplace_path preserved",
    context.marketplace_path == "internal/marketplace.json",
)
check("A: disabled_skills preserved", context.disabled_skills == ["risky-skill"])
check(
    "A: user_message_suffix preserved",
    context.user_message_suffix == "Follow the client's policy.",
)
check("A: current_datetime preserved exactly", context.current_datetime == FIXED_TIME)
check(
    "A: load_public_skills=False (skills were found)",
    context.load_public_skills is False,
)

# --- Part B: RemoteWorkspace, no skills found, base_context provided ---
base_context_b = AgentContext(
    marketplace_path="internal/marketplace.json",
    disabled_skills=["risky-skill"],
)
with patch.object(workspace, "_call_skills_api", return_value=[]):
    skills_b, context_b = workspace.load_skills_from_agent_server(
        base_context=base_context_b
    )

check("B: no skills returned", len(skills_b) == 0)
check(
    "B: marketplace_path preserved on fallback",
    context_b.marketplace_path == "internal/marketplace.json",
)
check(
    "B: disabled_skills preserved on fallback",
    context_b.disabled_skills == ["risky-skill"],
)
check("B: load_public_skills=True (fallback)", context_b.load_public_skills is True)

# --- Part C: no base_context given -> matches today's default behavior ---
with patch.object(
    workspace,
    "_call_skills_api",
    return_value=[{"name": "demo-skill", "content": "demo"}],
):
    _, context_c = workspace.load_skills_from_agent_server()

check(
    "C: default marketplace_path when no base_context",
    context_c.marketplace_path == AgentContext().marketplace_path,
)
check(
    "C: default disabled_skills when no base_context", context_c.disabled_skills == []
)
check(
    "C: default user_message_suffix when no base_context",
    context_c.user_message_suffix is None,
)

# --- Part D: OpenHandsCloudWorkspace override forwards base_context ---
with patch.object(OpenHandsCloudWorkspace, "model_post_init", lambda _self, _ctx: None):
    cloud_workspace = OpenHandsCloudWorkspace(
        cloud_api_url="https://test.com",
        cloud_api_key="test-key",
        local_agent_server_mode=True,
    )
    cloud_workspace._sandbox_id = "test-sandbox"
    cloud_workspace._session_api_key = "test-session"
    cloud_workspace.working_dir = "/workspace/project"
    cloud_workspace.host = "http://localhost:8000"

    cloud_base_context = AgentContext(marketplace_path="internal/marketplace.json")
    with patch.object(
        cloud_workspace,
        "_call_skills_api",
        return_value=[{"name": "demo-skill", "content": "demo"}],
    ):
        _, cloud_context = cloud_workspace.load_skills_from_agent_server(
            base_context=cloud_base_context
        )

check(
    "D: OpenHandsCloudWorkspace forwards base_context",
    cloud_context.marketplace_path == "internal/marketplace.json",
)

print()
if all(results):
    print(f"All {len(results)} checks passed.")
else:
    failed = len(results) - sum(results)
    print(f"{failed} of {len(results)} checks FAILED.")
    raise SystemExit(1)
