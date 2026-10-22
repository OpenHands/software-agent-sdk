"""The MCP client without an agent: create_mcp_tools against local servers.

Usage: client.py MODE

  transports  stdio + streamable HTTP + SSE (transport inferred from /sse)
              servers in one config; call echo on each.
  auth        one HTTP server reached with each static auth strategy; the
              server's `headers` tool reports what arrived.
  oauth       an expired stored token is refreshed against the token
              endpoint the server advertises (and against <origin>/token
              when it advertises none); a valid token is sent as is.
  timeout     a server that starts too slowly, and an unreachable URL.
  recovery    a transient HTTP 503 on a call, then the next calls; a
              closed client.
  restart     the session is lost for good: the HTTP server restarts, the
              stdio server exits; the next calls should reconnect.
  config      MCPServer validation, secret handling, config shapes, tool
              schemas, argument validation and tool serialization.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from base64 import urlsafe_b64encode
from pathlib import Path

from sdk_verify import emit, run_paths

from openhands.sdk.mcp import MCPServer, create_mcp_tools
from openhands.sdk.mcp.config import (
    MCPApiKeyAuthCredential,
    MCPBasicAuthCredential,
    MCPBearerAuthCredential,
    MCPHeaderAuthCredential,
    MCPNoneAuthCredential,
    coerce_mcp_config,
)


mode = sys.argv[1]
SERVER = str(Path(__file__).with_name("mcp_server.py"))
run = run_paths()["run"]
started: list[subprocess.Popen] = []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(transport: str, port: int, *extra: str, env=None) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, SERVER, transport, str(port), *extra],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, **(env or {})},
    )
    started.append(proc)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return proc
        except OSError:
            time.sleep(0.1)
    raise SystemExit(f"server on {port} did not start")


def stdio(*extra: str, **kwargs) -> MCPServer:
    return MCPServer(command=sys.executable, args=[SERVER, "stdio", *extra], **kwargs)


def text_of(observation) -> str:
    return "".join(getattr(c, "text", "") for c in observation.content)


def payload(text: str) -> str:
    """The tool's own output, without the "[Tool 'x' executed.]" prefix."""
    return text.split("]", 1)[1] if text.startswith("[Tool ") else text


def call(client, name, **arguments):
    tool = next(t for t in client.tools if t.name == name)
    observation = tool(tool.action_from_arguments(arguments))
    return text_of(observation), observation.is_error


def error_of(fn):
    try:
        fn()
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:400]


out = {"ok": True, "mode": mode}
try:
    if mode == "transports":
        http_port, sse_port = free_port(), free_port()
        start("http", http_port)
        start("sse", sse_port)
        config = {
            "local": stdio(),
            "web": MCPServer(url=f"http://127.0.0.1:{http_port}/mcp", transport="http"),
            "stream": MCPServer(url=f"http://127.0.0.1:{sse_port}/sse"),
        }
        with create_mcp_tools(config, timeout=30) as client:
            names = sorted(t.name for t in client.tools)
            out["tool_count"] = len(names)
            out["has_prefixed"] = [
                n for n in ("local_echo", "web_echo", "stream_echo") if n in names
            ]
            out["unprefixed_echo"] = "echo" in names
            out["echo"] = {
                p: call(client, f"{p}_echo", text=p)[0]
                for p in ("local", "web", "stream")
            }
        with create_mcp_tools({"only": stdio()}, timeout=30) as client:
            out["single_server_names_unprefixed"] = "echo" in {
                t.name for t in client.tools
            }

    elif mode == "auth":
        port = free_port()
        start("http", port)
        url = f"http://127.0.0.1:{port}/mcp"
        config = {
            "none": MCPServer(url=url, auth=MCPNoneAuthCredential(strategy="none")),
            "bearer": MCPServer(
                url=url,
                auth=MCPBearerAuthCredential(strategy="bearer", value="bear-tok"),
            ),
            "apikey": MCPServer(
                url=url,
                auth=MCPApiKeyAuthCredential(
                    strategy="api_key", value="key-1", header_name="X-Api-Key"
                ),
            ),
            "basic": MCPServer(
                url=url,
                auth=MCPBasicAuthCredential(
                    strategy="basic", username="ann", password="pw"
                ),
            ),
            "custom": MCPServer(
                url=url,
                auth=MCPHeaderAuthCredential(
                    strategy="header", headers={"X-Team": "blue"}
                ),
            ),
        }
        with create_mcp_tools(config, timeout=30) as client:
            for name in config:
                headers = json.loads(payload(call(client, f"{name}_headers")[0]))
                out[name] = {
                    k: v
                    for k, v in headers.items()
                    if k in ("authorization", "x-api-key", "x-team")
                }

    elif mode == "oauth":
        from key_value.aio.stores.memory import MemoryStore

        from openhands.sdk.mcp import MCPOAuthAuthCredential, MCPOAuthAuthentication

        async def stored(url: str, expires_at: float) -> MemoryStore:
            store = MemoryStore()
            await store.put(
                key=f"{url}/tokens",
                collection="mcp-oauth-token",
                value={
                    "access_token": "stored-access-token",
                    "refresh_token": "stored-refresh-token",
                    "token_type": "Bearer",
                    "expires_in": 7200,
                },
            )
            await store.put(
                key=f"{url}/token_expiry",
                collection="mcp-oauth-token-expiry",
                value={"expires_at": expires_at},
            )
            await store.put(
                key=f"{url}/client_info",
                collection="mcp-oauth-client-info",
                value={
                    "client_id": "client-id",
                    "redirect_uris": ["http://127.0.0.1/callback"],
                    "token_endpoint_auth_method": "none",
                },
            )
            return store

        for case, extra, expires in [
            ("valid", [], time.time() + 3600),
            ("expired", [], time.time() - 60),
            ("expired_no_metadata", ["--no-metadata"], time.time() - 60),
        ]:
            log = run / f"oauth-{case}.log"
            port = free_port()
            start("http", port, "--oauth", *extra, env={"VERIFY_OAUTH_LOG": str(log)})
            url = f"http://127.0.0.1:{port}/mcp"
            store = asyncio.run(stored(url, expires))
            config = {
                "secure": MCPServer(
                    url=url,
                    auth=MCPOAuthAuthCredential(
                        strategy="oauth2",
                        authentication=MCPOAuthAuthentication(
                            type="oauth", client_id="client-id"
                        ),
                    ),
                )
            }
            result = {}
            try:
                with create_mcp_tools(
                    config, timeout=20, mcp_oauth_token_storage=store
                ) as client:
                    headers = json.loads(payload(call(client, "headers")[0]))
                    result["authorization"] = headers.get("authorization")
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"[:300]
            result["server_log"] = log.read_text().splitlines() if log.exists() else []
            out[case] = result

    elif mode == "timeout":
        started_at = time.time()
        out["slow_error"] = error_of(
            lambda: create_mcp_tools({"sleepy": stdio("--slow-start", "20")}, timeout=2)
        )
        out["slow_seconds"] = round(time.time() - started_at, 1)
        out["unreachable_error"] = error_of(
            lambda: create_mcp_tools(
                {"gone": MCPServer(url=f"http://127.0.0.1:{free_port()}/mcp")},
                timeout=5,
            )
        )
        out["all_disabled_error"] = error_of(
            lambda: create_mcp_tools({"off": stdio(enabled=False)}, timeout=5)
        )
        with create_mcp_tools(
            {
                "on": stdio(),
                "off": MCPServer(command="sh", args=["-c", "exit 1"], enabled=False),
            },
            timeout=30,
        ) as client:
            out["mixed_enabled_names_sample"] = sorted(t.name for t in client.tools)[:3]

    elif mode == "recovery":
        port = free_port()
        start("http", port, "--fail-first-call")
        with create_mcp_tools(
            {"flaky": MCPServer(url=f"http://127.0.0.1:{port}/mcp")}, timeout=30
        ) as client:
            out["transient_first"] = call(client, "counter")
            out["transient_second"] = call(client, "counter")
            out["transient_third"] = call(client, "counter")
        with create_mcp_tools({"local": stdio()}, timeout=30) as client:
            client.sync_close()
            out["closed_client"] = call(client, "counter")

    elif mode == "restart":
        port = free_port()
        proc = start("http", port)
        with create_mcp_tools(
            {"web": MCPServer(url=f"http://127.0.0.1:{port}/mcp")}, timeout=30
        ) as client:
            out["http_before"] = call(client, "counter")
            proc.kill()
            proc.wait()
            start("http", port)
            out["http_after_restart"] = call(client, "counter")
            out["http_after_restart_again"] = call(client, "counter")
        with create_mcp_tools({"local": stdio()}, timeout=30) as client:
            out["stdio_before"] = call(client, "counter")
            out["stdio_die"] = call(client, "die")
            time.sleep(1.5)
            out["stdio_after_exit"] = call(client, "counter")
            out["stdio_after_exit_again"] = call(client, "counter")

    elif mode == "config":
        from openhands.sdk.utils.cipher import Cipher

        out["unknown_field_error"] = (
            error_of(lambda: MCPServer(command="x", colour="red")) is not None
        )
        out["stdio_without_command_error"] = (
            error_of(lambda: MCPServer(transport="stdio")) is not None
        )
        out["http_without_url_error"] = (
            error_of(lambda: MCPServer(transport="http")) is not None
        )
        out["auth_and_header_error"] = (
            error_of(
                lambda: MCPServer(
                    url="http://h/mcp",
                    headers={"Authorization": "Bearer x"},
                    auth=MCPBearerAuthCredential(strategy="bearer", value="y"),
                )
            )
            is not None
        )
        external = {"mcpServers": {"x": {"command": "echo", "args": ["hi"]}}}
        coerced = coerce_mcp_config(external)
        out["coerce_external"] = (
            sorted(coerced) if isinstance(coerced, dict) else str(type(coerced))
        )
        out["create_rejects_external"] = error_of(
            lambda: create_mcp_tools(external, timeout=5)
        )
        server = MCPServer(command="x", env={"TOKEN": "env-secret"}, headers=None)
        remote = MCPServer(url="http://h/mcp", headers={"X-Key": "header-secret"})
        dumped = json.dumps(
            [server.model_dump(mode="json"), remote.model_dump(mode="json")]
        )
        out["secret_in_plain_dump"] = (
            "env-secret" in dumped or "header-secret" in dumped
        )
        cipher = Cipher(urlsafe_b64encode(b"m" * 32).decode("ascii"))
        sealed = server.model_dump(mode="json", context={"cipher": cipher})
        out["secret_in_cipher_dump"] = "env-secret" in json.dumps(sealed)
        out["cipher_roundtrip"] = (
            MCPServer.model_validate(sealed, context={"cipher": cipher})
            .env["TOKEN"]
            .get_secret_value()
        )

        with create_mcp_tools({"v": stdio()}, timeout=30) as client:
            tools = {t.name: t for t in client.tools}
            nested = tools["nested"]
            openai = nested.to_openai_tool()["function"]["parameters"]
            risky = nested.to_openai_tool(add_security_risk_prediction=True)[
                "function"
            ]["parameters"]
            responses = nested.to_responses_tool()
            out["openai_props"] = sorted(openai["properties"])
            out["openai_required"] = openai.get("required")
            out["security_risk_added"] = (
                "security_risk" in risky["properties"]
                and "security_risk" not in openai["properties"]
            )
            out["responses_name"] = responses.get("name")
            out["responses_nested_type"] = (
                responses.get("parameters", {})
                .get("properties", {})
                .get("config", {})
                .get("type")
            )
            out["annotations"] = (
                nested.annotations.model_dump(exclude_none=True)
                if nested.annotations
                else None
            )
            action = nested.action_from_arguments(
                {
                    "config": {"title": "t", "tags": ["x"], "limits": {"max": 1}},
                    "note": None,
                }
            )
            out["extras_direct_error"] = (
                error_of(
                    lambda: nested.action_from_arguments(
                        {
                            "config": {"title": "t", "tags": [], "limits": {"max": 1}},
                            "summary": "doing it",
                            "security_risk": "LOW",
                        }
                    )
                )
                is not None
            )
            out["mcp_arguments"] = action.to_mcp_arguments()
            kind_action = tools["kind_echo"].action_from_arguments({"kind": "keep-me"})
            out["kind_arguments"] = kind_action.to_mcp_arguments()
            out["kind_call"] = text_of(tools["kind_echo"](kind_action))
            out["invalid_error"] = (
                error_of(
                    lambda: tools["add"].action_from_arguments({"a": "not-a-number"})
                )
                is not None
            )
            out["missing_error"] = (
                error_of(lambda: tools["nested"].action_from_arguments({})) is not None
            )
            fail = tools["fail"](tools["fail"].action_from_arguments({}))
            pic = tools["picture"](tools["picture"].action_from_arguments({}))
            st = tools["structured"](tools["structured"].action_from_arguments({}))
            out["fail"] = {"is_error": fail.is_error, "text": text_of(fail)[:120]}
            out["picture_content"] = [type(c).__name__ for c in pic.content]
            out["structured_text"] = text_of(st)
            dumped_tool = nested.model_dump_json()
            out["wire_spelling"] = (
                "inputSchema" in dumped_tool and "input_schema" not in dumped_tool
            )
            from openhands.sdk.tool import ToolDefinition

            restored = ToolDefinition.model_validate_json(dumped_tool)
            out["restored_type"] = type(restored).__name__
            out["restored_same_schema"] = (
                restored.to_openai_tool() == nested.to_openai_tool()
            )
            legacy = json.loads(dumped_tool)
            mcp_tool = legacy.get("mcp_tool", {})
            out["legacy_key_rewritten"] = "inputSchema" in mcp_tool
            if "inputSchema" in mcp_tool:
                mcp_tool["input_schema"] = mcp_tool.pop("inputSchema")
            out["legacy_spelling_loads"] = (
                error_of(lambda: ToolDefinition.model_validate(legacy)) is None
            )
            out["frozen"] = (
                error_of(lambda: setattr(nested, "description", "x")) is not None
            )
            out["kind_in_dump"] = json.loads(dumped_tool).get("kind")
finally:
    for proc in started:
        proc.kill()
emit(out)
