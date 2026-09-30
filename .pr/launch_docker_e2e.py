"""Live check of the Docker runtime split (#5398): host resolves, container finalizes.

Runs a host agent-server in Docker runtime mode, so each conversation gets its
own container started from ``--image``. Pass an image whose browser capability
differs from the host's, and the launched agent's tools show which side decided.
The container's HOME has no stores, so routing only works if the host copied the
meta-profile and its LLMs in.

    python .pr/launch_docker_e2e.py --sdk <sdk checkout> --image <agent-server image> \
        --mock-llm <mock-llm-server.py>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


DATETIME_LINE = re.compile(r"current date and time is[^\n]*", re.IGNORECASE)


def wait_ready(url: str, process: subprocess.Popen, home: Path) -> None:
    for _ in range(240):
        if process.poll() is not None:
            sys.exit(f"{url} exited early; see {home}")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    sys.exit(f"{url} never became ready")


def running_containers() -> set[str]:
    out = subprocess.run(
        ["docker", "ps", "--filter", "name=agent-server-conversation-", "-q"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return set(out.split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--mock-llm", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18590)
    parser.add_argument("--llm-port", type=int, default=18599)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--container-browser",
        choices=["yes", "no"],
        default="yes",
        help="Whether the container image can run the browser tool set.",
    )
    args = parser.parse_args()

    home = Path(tempfile.mkdtemp(prefix="launch-docker-e2e-"))
    print(f"state: {home}", flush=True)
    (home / "workspace").mkdir()
    tmux = Path(f"/tmp/ohd{args.port}")
    tmux.mkdir(exist_ok=True)
    python = str(args.sdk / ".venv" / "bin" / "python")
    env = {
        **os.environ,
        "OH_PERSISTENCE_DIR": str(home / "persistence"),
        "OH_CONVERSATIONS_PATH": str(home / "conversations"),
        "OH_WORKSPACE_PATH": str(home / "workspace"),
        "OH_SECRET_KEY": "launch-docker-e2e-key",
        "OH_CONVERSATION_RUNTIME": "docker",
        "OH_CONVERSATION_IMAGE": args.image,
        "OH_CONVERSATION_CONTAINER_STARTUP_TIMEOUT": "240",
        "OPENHANDS_SUPPRESS_BANNER": "1",
        "TMUX_TMPDIR": str(tmux),
        "OH_ENABLE_VSCODE": "false",
    }
    llm_url = f"http://127.0.0.1:{args.llm_port}"
    base = f"http://127.0.0.1:{args.port}"
    llm_proc = subprocess.Popen(
        [python, str(args.mock_llm), "--port", str(args.llm_port)],
        env=env,
        stdout=(home / "mock-llm.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    server = subprocess.Popen(
        [python, "-m", "openhands.agent_server", "--port", str(args.port)],
        env=env,
        cwd=home,
        stdout=(home / "server.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    rows: dict[str, Any] = {}
    verdicts: dict[str, bool] = {}
    conversation_ids: list[str] = []
    client = httpx.Client(base_url=base, timeout=300)
    try:
        wait_ready(f"{llm_url}/", llm_proc, home)
        wait_ready(f"{base}/health", server, home)

        def reply_with(turns: int = 20) -> None:
            httpx.post(f"{llm_url}/admin/reset")
            httpx.post(
                f"{llm_url}/admin/trajectory/register",
                json={"name": "done", "turns": [{"text": "DONE"}] * turns},
            )
            httpx.post(f"{llm_url}/admin/trajectory/activate", json={"name": "done"})

        # The container reaches the host's mock LLM through Docker Desktop.
        container_llm = f"http://host.docker.internal:{args.llm_port}"
        client.patch(
            "/api/settings",
            json={"agent_settings_diff": {"agent_context": {"load_memory": True}}},
        ).raise_for_status()
        for name, model in (
            ("mock", "openai/mock-test-model"),
            ("mock-fast", "openai/mock-fast-model"),
        ):
            client.post(
                f"/api/profiles/{name}",
                json={
                    "llm": {"model": model, "base_url": container_llm, "api_key": "sk"},
                    "include_secrets": True,
                },
            ).raise_for_status()
        client.post(
            "/api/meta-profiles/router",
            json={
                "classifier_model": "mock",
                "classes": [{"description": "everything", "model": "mock-fast"}],
            },
        ).raise_for_status()
        profile = {
            "llm_profile_ref": "mock",
            "system_message_suffix": "DOCKER_PROFILE_SUFFIX",
            "secret_refs": ["ALLOWED"],
            "mcp_server_refs": [],
            "condenser": {"kind": "NoOpCondenser"},
        }
        routed = {
            **profile,
            "enable_classify_and_switch_llm_tool": True,
            "meta_profile_ref": "router",
        }
        saved = client.post("/api/agent-profiles/docker-profile", json=routed)
        rows["save profile with meta_profile_ref"] = {"status": saved.status_code}
        if saved.status_code != 201:
            client.post(
                "/api/agent-profiles/docker-profile", json=profile
            ).raise_for_status()
        client.post(
            "/api/agent-profiles/broken",
            json={**profile, "llm_profile_ref": "gone", "mcp_server_refs": ["x"]},
        ).raise_for_status()
        ids = {
            p["name"]: p["id"]
            for p in client.get("/api/agent-profiles").json()["profiles"]
        }

        preview = client.post("/api/agent-profiles/docker-profile/materialize").json()
        rows["host materialize"] = {
            k: preview.get(k) for k in ("valid", "resolved_tools", "pending", "errors")
        }

        reply_with()
        before = running_containers()
        response = client.post(
            "/api/conversations",
            params={"include_skills": "true"},
            json={
                "agent_profile_id": ids["docker-profile"],
                "workspace": {"kind": "LocalWorkspace", "working_dir": "/workspace"},
                "autotitle": False,
                "secrets": {
                    "ALLOWED": {"kind": "StaticSecret", "value": "allowed-value"},
                    "OTHER": {"kind": "StaticSecret", "value": "other-value"},
                },
                "initial_message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                    "run": True,
                },
            },
        )
        row: dict[str, Any] = {"status": response.status_code}
        if response.status_code in (200, 201):
            info = response.json()
            conversation_ids.append(info["id"])
            for _ in range(240):
                state = client.get(f"/api/conversations/{info['id']}").json()
                if state.get("execution_status") not in ("running", "idle"):
                    break
                time.sleep(0.5)
            requests = httpx.get(f"{llm_url}/admin/requests").json()["requests"]
            body = requests[0] if requests else {}
            system = "\n".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for message in body.get("messages", [])
                if message["role"] == "system"
                for part in (
                    message["content"]
                    if isinstance(message["content"], list)
                    else [message["content"]]
                )
            )
            routing = next(
                (
                    t
                    for t in info["agent"]["tools"]
                    if t["name"] == "ClassifyAndSwitchLLMTool"
                ),
                None,
            )
            stamp = DATETIME_LINE.search(system)
            row.update(
                {
                    "secrets_seen": sorted(
                        name for name in ("ALLOWED", "OTHER") if name in system
                    ),
                    "model_tools": sorted(
                        t["function"]["name"] for t in body.get("tools") or []
                    ),
                    "agent_tools": [t["name"] for t in info["agent"]["tools"]],
                    "suffix": "DOCKER_PROFILE_SUFFIX" in system,
                    "datetime": stamp.group(0) if stamp else None,
                    "launched_agent_profile": info.get("launched_agent_profile"),
                    "load_memory": (info["agent"].get("agent_context") or {}).get(
                        "load_memory"
                    ),
                    "routing_inline_meta_profile": bool(
                        routing and (routing.get("params") or {}).get("meta_profile")
                    ),
                    "routing_llms": sorted(
                        ((routing or {}).get("params") or {}).get("meta_profile_llms")
                        or {}
                    ),
                    "containers_started": len(running_containers() - before),
                }
            )
        else:
            row["detail"] = response.text[:400]
        rows["profile launch through the Docker host"] = row

        if conversation_ids:
            cid = conversation_ids[0]
            before = running_containers()
            again = client.post(
                "/api/conversations",
                json={
                    "conversation_id": cid,
                    "agent_profile_id": ids["broken"],
                    "workspace": {
                        "kind": "LocalWorkspace",
                        "working_dir": "/workspace",
                    },
                },
            )
            rows["re-post existing conversation with a broken profile"] = {
                "status": again.status_code,
                "container_still_running": before <= running_containers(),
            }

        for label, source in (
            (
                "agent_settings through the Docker host",
                {
                    "agent_settings": {
                        "llm": {
                            "model": "openai/mock-test-model",
                            "base_url": container_llm,
                            "api_key": "sk",
                        },
                        "agent_context": {
                            "current_datetime": "2020-01-01T00:00:00+00:00"
                        },
                    }
                },
            ),
            (
                "raw agent through the Docker host",
                {
                    "agent": {
                        "kind": "Agent",
                        "llm": {
                            "model": "openai/mock-test-model",
                            "base_url": container_llm,
                            "api_key": "sk",
                        },
                        "tools": [{"name": "terminal"}],
                        "agent_context": {
                            "current_datetime": "2020-01-01T00:00:00+00:00"
                        },
                    }
                },
            ),
        ):
            reply_with()
            launched = client.post(
                "/api/conversations",
                params={"include_skills": "true"},
                json={
                    **source,
                    "workspace": {
                        "kind": "LocalWorkspace",
                        "working_dir": "/workspace",
                    },
                    "autotitle": False,
                    "initial_message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "hello"}],
                        "run": True,
                    },
                },
            )
            entry: dict[str, Any] = {"status": launched.status_code}
            if launched.status_code in (200, 201):
                info = launched.json()
                conversation_ids.append(info["id"])
                for _ in range(240):
                    state = client.get(f"/api/conversations/{info['id']}").json()
                    if state.get("execution_status") not in ("running", "idle"):
                        break
                    time.sleep(0.5)
                seen = httpx.get(f"{llm_url}/admin/requests").json()["requests"]
                system = json.dumps((seen[0] if seen else {}).get("messages", []))
                stamp = DATETIME_LINE.search(system)
                entry.update(
                    {
                        "agent_tools": [t["name"] for t in info["agent"]["tools"]],
                        "datetime": stamp.group(0) if stamp else None,
                        "load_memory": (info["agent"].get("agent_context") or {}).get(
                            "load_memory"
                        ),
                        "model_called": bool(seen),
                    }
                )
            else:
                entry["detail"] = launched.text[:300]
            rows[label] = entry

        before = running_containers()
        broken = client.post(
            "/api/conversations",
            json={
                "agent_profile_id": ids["broken"],
                "workspace": {"kind": "LocalWorkspace", "working_dir": "/workspace"},
            },
        )
        rows["broken profile through the Docker host"] = {
            "status": broken.status_code,
            "detail": broken.json().get("detail"),
            "containers_started": len(running_containers() - before),
        }

        saved = client.post(
            "/api/agent-profiles/broken-router",
            json={**routed, "meta_profile_ref": "missing-router"},
        )
        dangling_meta: dict[str, Any] = {"save_status": saved.status_code}
        if saved.status_code == 201:
            router_id = next(
                p["id"]
                for p in client.get("/api/agent-profiles").json()["profiles"]
                if p["name"] == "broken-router"
            )
            before = running_containers()
            launched = client.post(
                "/api/conversations",
                json={
                    "agent_profile_id": router_id,
                    "workspace": {
                        "kind": "LocalWorkspace",
                        "working_dir": "/workspace",
                    },
                },
            )
            dangling_meta.update(
                {
                    "status": launched.status_code,
                    "detail": launched.json().get("detail"),
                    "containers_started": len(running_containers() - before),
                }
            )
        rows["dangling meta_profile_ref through the Docker host"] = dangling_meta
    finally:
        for cid in conversation_ids:
            try:
                client.delete(f"/api/conversations/{cid}")
            except httpx.HTTPError:
                pass
        for process in (server, llm_proc):
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()

    for name, row in rows.items():
        print(f"\n## {name}\n{json.dumps(row, indent=2, default=str)}")

    launch = rows.get("profile launch through the Docker host", {})
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    verdicts["host preview leaves browser to the container"] = rows[
        "host materialize"
    ].get("pending") == ["browser_tool_set"]
    verdicts["browser follows the container, not the host"] = (
        "browser_tool_set" in (launch.get("agent_tools") or [])
    ) is (args.container_browser == "yes")
    verdicts["profile suffix reaches the model"] = bool(launch.get("suffix"))
    verdicts["provenance recorded in the container"] = (
        launch.get("launched_agent_profile") or {}
    ).get("agent_profile_id") == ids.get("docker-profile")
    verdicts["routing works without stores in the container"] = bool(
        launch.get("routing_inline_meta_profile")
    ) and launch.get("routing_llms") == ["mock", "mock-fast"]
    verdicts["memory preference forwarded"] = launch.get("load_memory") is True
    verdicts["fresh timestamp"] = today in (launch.get("datetime") or "")
    repost = rows.get("re-post existing conversation with a broken profile", {})
    verdicts["re-post of a live conversation leaves it running"] = repost.get(
        "status"
    ) == 200 and bool(repost.get("container_still_running"))
    broken_row = rows["broken profile through the Docker host"]
    detail = broken_row.get("detail") or {}
    verdicts["dangling refs fail on the host, no container"] = (
        broken_row["status"] == 422
        and isinstance(detail, dict)
        and detail.get("dangling_llm_profile_ref") == "gone"
        and broken_row["containers_started"] == 0
    )
    meta_row = rows["dangling meta_profile_ref through the Docker host"]
    meta_detail = meta_row.get("detail") or {}
    verdicts["dangling meta_profile_ref fails on the host, no container"] = (
        meta_row.get("status") == 422
        and isinstance(meta_detail, dict)
        and meta_detail.get("dangling_meta_profile_ref") == "missing-router"
        and meta_row.get("containers_started") == 0
    )
    verdicts["secret_refs scope secrets in the container"] = launch.get(
        "secrets_seen"
    ) == ["ALLOWED"]
    for label in (
        "agent_settings through the Docker host",
        "raw agent through the Docker host",
    ):
        entry = rows.get(label, {})
        verdicts[f"{label}: container finalizes (fresh timestamp, memory)"] = (
            entry.get("status") in (200, 201)
            and today in (entry.get("datetime") or "")
            and entry.get("load_memory") is True
        )
    settings_entry = rows.get("agent_settings through the Docker host", {})
    verdicts["agent_settings default tools follow the container"] = (
        "browser_tool_set" in (settings_entry.get("agent_tools") or [])
    ) is (args.container_browser == "yes")
    print("\n# Verdicts")
    for name, ok in verdicts.items():
        print(f"- {'PASS' if ok else 'FAIL'}  {name}")
    if args.out:
        args.out.write_text(json.dumps({"rows": rows, "verdicts": verdicts}, indent=2))
    return 0 if all(verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
