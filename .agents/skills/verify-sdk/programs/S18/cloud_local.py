"""OpenHandsCloudWorkspace in local agent-server mode, the way an automation
script runs inside a Cloud sandbox: the agent server is the repo's server on
localhost (AGENT_SERVER_PORT), and the Cloud API it reads the account's LLM,
secrets and MCP servers from is a local stand-in that records each request.
A scripted model endpoint plays the account's LLM.
"""

import json
import os

import httpx
from sdk_verify import emit, run_paths
from servers import AgentServer, CaptureServer, FakeLLMServer

from openhands.sdk import Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool
from openhands.workspace import OpenHandsCloudWorkspace


run = run_paths()["run"]
KEY = "sandbox-session-key"
work = run / "sandbox-workspace"
work.mkdir(exist_ok=True)
result: dict = {"ok": True}

with (
    FakeLLMServer(
        [
            {"tool": "terminal", "args": {"command": "echo cloud > cloud.txt"}},
            {"finish": "ok"},
        ]
    ) as fake,
    CaptureServer() as cloud,
):
    me = {
        "llm_model": "openai/cloud-model",
        "llm_api_key": "account-llm-key",
        "llm_base_url": f"{fake.base_url}/v1",
        "llm_profiles": {"profiles": {"fast": {"model": "openai/fast-model"}}},
        "mcp_config": {
            "sse_servers": [{"url": "http://127.0.0.1:9/sse", "api_key": "sse-key"}],
            "shttp_servers": [{"url": "http://127.0.0.1:9/mcp", "timeout": 30}],
            "stdio_servers": [{"name": "fs", "command": "npx", "args": ["fs-server"]}],
        },
    }
    # The first account read fails with a 500: get_llm retries it.
    cloud.routes["/api/v1/users/me"] = [(500, {"error": "flaky"}), (200, me)]
    cloud.routes["/api/v1/sandboxes/sbx-1/settings/secrets"] = (
        200,
        {
            "secrets": [
                {"name": "GITHUB_TOKEN", "description": "gh"},
                {"name": "NPM_TOKEN"},
            ]
        },
    )
    with AgentServer(run / "agent-server", env={"SESSION_API_KEY": KEY}) as server:
        os.environ.update(
            {
                "AGENT_SERVER_PORT": str(server.port),
                "SANDBOX_ID": "sbx-1",
                "SESSION_API_KEY": KEY,
                "AUTOMATION_CALLBACK_URL": f"{cloud.base_url}/callback",
                "AUTOMATION_RUN_ID": "run-7",
                "AUTOMATION_EVENT_PAYLOAD": json.dumps(
                    {
                        "trigger": "cron",
                        "automation_id": "a1",
                        "automation_name": "Nightly",
                    }
                ),
            }
        )
        with OpenHandsCloudWorkspace(
            local_agent_server_mode=True,
            cloud_api_url=cloud.base_url + "/",
            cloud_api_key="cloud-key",
            working_dir=str(work),
        ) as ws:
            result["host"] = ws.host == f"http://localhost:{server.port}"
            result["session_key_from_env"] = ws.api_key == KEY
            r = ws.execute_command("echo in-sandbox && pwd", cwd=str(work))
            result["exec"] = (r.stdout or "").split()
            result["alive"] = ws.alive

            before = len(cloud.requests)
            llm = ws.get_llm()
            result["get_llm"] = {
                "model": llm.model,
                "base_url_ok": llm.base_url == f"{fake.base_url}/v1",
                "requests": len(cloud.requests) - before,  # 500 then 200
            }
            result["get_llm_override"] = ws.get_llm(model="openai/other").model
            result["get_llm_profile"] = ws.get_llm(profile_name="fast").model
            try:
                ws.get_llm(profile_name="nope")
                result["missing_profile"] = None
            except FileNotFoundError:
                result["missing_profile"] = "FileNotFoundError"
            secrets = ws.get_secrets()
            gh = secrets.get("GITHUB_TOKEN")
            result["secrets"] = sorted(secrets)
            result["secret_url_ok"] = bool(gh) and gh.url.endswith(
                "/api/v1/sandboxes/sbx-1/settings/secrets/GITHUB_TOKEN"
            )
            result["secret_session_header"] = (
                bool(gh) and gh.headers.get("X-Session-API-Key") == KEY
            )
            result["secrets_filtered"] = sorted(ws.get_secrets(names=["NPM_TOKEN"]))
            mcp = ws.get_mcp_config()
            result["mcp"] = {
                k: getattr(v, "transport", None) or "stdio"
                for k, v in sorted(mcp.items())
            }

            # A client error is not retried.
            cloud.routes["/api/v1/users/me"] = (403, {"error": "forbidden"})
            before = len(cloud.requests)
            try:
                ws.get_mcp_config()
                result["forbidden_error"] = None
            except httpx.HTTPStatusError as exc:
                result["forbidden_error"] = exc.response.status_code
            result["forbidden_requests"] = len(cloud.requests) - before

            result["default_tags"] = ws.default_conversation_tags
            agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
            conv = Conversation(
                agent=agent, workspace=ws, tags={"team": "infra"}, visualizer=None
            )
            conv.send_message("write cloud.txt")
            conv.run()
            cid = str(conv.state.id)
            info = httpx.get(
                f"{server.base_url}/api/conversations/{cid}",
                headers={"X-Session-API-Key": KEY},
            ).json()
            result["conversation_tags"] = info.get("tags")
            result["conversation_status"] = str(conv.state.execution_status.value)
            result["llm_requests"] = len(fake.requests)
            result["agent_file"] = (
                (work / "cloud.txt").read_text().strip()
                if (work / "cloud.txt").exists()
                else None
            )
            conv.close()
        posts = [r for r in cloud.requests if r["path"] == "/callback"]
        body = posts[0]["body"] if posts else {}
        result["callback"] = {
            "posts": len(posts),
            "status": body.get("status"),
            "run_id": body.get("run_id"),
            "conversation_id_ok": body.get("conversation_id") == cid,
            "auth": posts[0]["headers"].get("authorization") if posts else None,
        }
        result["sandbox_deletes"] = sum(
            1 for r in cloud.requests if r["method"] == "DELETE"
        )
        result["sandbox_creates"] = sum(
            1
            for r in cloud.requests
            if r["method"] == "POST" and "sandboxes" in r["path"]
        )

emit(result)
