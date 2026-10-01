#!/usr/bin/env python
"""Live Docker-runtime check for #5310.

Runs a real outer agent-server in Docker conversation-runtime mode from
``--server-root``, seeds a temporary host profile store with one selected
auxiliary profile plus one unrelated profile, starts a Docker-backed
conversation with ``title_llm_profile`` set, sends one user message
(``run=false``), and waits for the auto-generated title.

A deterministic OpenAI-compatible HTTP endpoint on the host records every
request and answers with a title that names the model it was asked for, so the
observed title says which LLM produced it:

- ``synthetic-title-model`` (the auxiliary profile) -> ``Aux profile title 5310``
- any other model (the agent's own LLM)             -> ``Agent LLM title 5310``
- no LLM call at all (truncation fallback)           -> the message prefix

Only synthetic credentials are used; nothing here talks to a real provider.
This is configuration/transport evidence, not evidence of provider
compatibility or model quality.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import httpx
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.request import (
    SendMessageRequest,
    StartConversationRequest,
)
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.message import TextContent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


OUTER_KEY = "outer-host-secret-key-5310"
SESSION_KEY = "outer-session-key-5310"
AUX_PROFILE = "title-aux"
AUX_MODEL = "openai/synthetic-title-model"
AUX_KEY = "synthetic-aux-key-5310-not-a-real-credential"
UNRELATED_PROFILE = "unrelated"
UNRELATED_KEY = "unrelated-profile-secret-must-not-be-copied"
AGENT_KEY = "synthetic-agent-key-5310"
AUX_TITLE = "Aux profile title 5310"
AGENT_TITLE = "Agent LLM title 5310"
MESSAGE = "Please investigate the flaky login test in CI and propose a fix"
OWNER_LABEL = "ai.openhands.runtime-owner"


class SyntheticEndpoint(http.server.BaseHTTPRequestHandler):
    records: ClassVar[list[dict]] = []

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/v1/models":
            self._send(
                200,
                {
                    "object": "list",
                    "data": [{"id": "synthetic-title-model", "object": "model"}],
                },
            )
            return
        self._send(404, {"error": f"unexpected GET {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}
        auth = self.headers.get("Authorization") or ""
        credential = "none"
        for label, key in (
            ("aux", AUX_KEY),
            ("agent", AGENT_KEY),
            ("unrelated", UNRELATED_KEY),
        ):
            if key in auth:
                credential = label
        model = payload.get("model")
        SyntheticEndpoint.records.append(
            {"path": self.path, "model": model, "credential": credential}
        )
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, {"error": f"unexpected POST {self.path}"})
            return
        title = AUX_TITLE if model == "synthetic-title-model" else AGENT_TITLE
        self._send(
            200,
            {
                "id": "chatcmpl-5310",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": title},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    def log_message(self, *_args) -> None:  # silence default stderr logging
        return


def run(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def find_container(conversations_dir: Path) -> str | None:
    listed = run(["docker", "ps", "-q", "--filter", f"label={OWNER_LABEL}"])
    for cid in listed.stdout.split():
        inspect = run(["docker", "inspect", cid])
        if inspect.returncode != 0:
            continue
        for mount in json.loads(inspect.stdout)[0].get("Mounts", []):
            if str(conversations_dir) in mount.get("Source", ""):
                return cid
    return None


def container_mounts(cid: str) -> list[dict]:
    inspect = run(["docker", "inspect", cid])
    return [
        {"source": m.get("Source"), "destination": m.get("Destination")}
        for m in json.loads(inspect.stdout)[0].get("Mounts", [])
    ]


def wait_for(predicate, timeout: float, interval: float = 0.5, what: str = ""):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout:.0f}s waiting for {what}")


def scan_for_secret(paths: list[Path], needle: str) -> list[str]:
    hits = []
    for root in paths:
        if not root.exists():
            continue
        files = (
            [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        )
        for path in files:
            try:
                if needle.encode() in path.read_bytes():
                    hits.append(str(path))
            except OSError:
                pass
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-root", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument(
        "--expect", choices=("aux", "agent", "truncation"), required=True
    )
    parser.add_argument(
        "--agent-llm", choices=("synthetic", "acp-sentinel"), default="synthetic"
    )
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--label", default="run")
    parser.add_argument(
        "--restart", action="store_true", help="release + reprovision the runtime"
    )
    parser.add_argument(
        "--repeat-starts",
        action="store_true",
        help="repeat the start request after host edits; the snapshot must not change",
    )
    parser.add_argument("--title-timeout", type=float, default=120)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    args.server_root = args.server_root.resolve()
    server_python = args.server_root / ".venv" / "bin" / "python"
    if not server_python.exists():
        print(f"missing venv python at {server_python}", file=sys.stderr)
        return 2
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "label": args.label,
        "server_root": str(args.server_root.resolve()),
        "server_git_head": run(
            ["git", "-C", str(args.server_root), "rev-parse", "HEAD"]
        ).stdout.strip(),
        "server_dirty_files": run(
            ["git", "-C", str(args.server_root), "status", "--porcelain"]
        )
        .stdout.strip()
        .splitlines(),
        "image": args.image,
        "image_id": run(
            ["docker", "images", "-q", "--no-trunc", args.image]
        ).stdout.strip(),
        "expect": args.expect,
        "agent_llm": args.agent_llm,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # 1. deterministic local model endpoint (loopback; reachable from the container
    #    through host.docker.internal on Docker Desktop, proven separately below)
    SyntheticEndpoint.records = []
    endpoint = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SyntheticEndpoint)
    endpoint_port = endpoint.server_address[1]
    threading.Thread(target=endpoint.serve_forever, daemon=True).start()
    base_url = f"http://host.docker.internal:{endpoint_port}/v1"
    summary["endpoint"] = base_url

    # 2. temporary outer state: nothing under the real ~/.openhands is touched
    root = Path(tempfile.mkdtemp(prefix="oh5310-")).resolve()
    home = root / "home"
    persistence = root / "persistence"
    conversations = root / "conversations"
    workspaces = root / "workspaces"
    for d in (home, persistence, conversations, workspaces):
        d.mkdir(parents=True, mode=0o700)
    outer_cipher = Cipher(OUTER_KEY)
    host_store = LLMProfileStore(base_dir=persistence / "profiles")
    host_store.save(
        AUX_PROFILE,
        LLM(
            model=AUX_MODEL,
            base_url=base_url,
            api_key=SecretStr(AUX_KEY),
            usage_id="title-aux",
        ),
        include_secrets=True,
        cipher=outer_cipher,
    )
    host_store.save(
        UNRELATED_PROFILE,
        LLM(
            model="openai/unrelated-model",
            base_url=base_url,
            api_key=SecretStr(UNRELATED_KEY),
        ),
        include_secrets=True,
        cipher=outer_cipher,
    )
    summary["host_profiles"] = sorted(host_store.list())

    port_probe = __import__("socket").socket()
    port_probe.bind(("127.0.0.1", 0))
    server_port = port_probe.getsockname()[1]
    port_probe.close()
    config = {
        "session_api_keys": [SESSION_KEY],
        "conversations_path": str(conversations),
        "workspace_path": str(workspaces),
        "conversation_runtime": "docker",
        "conversation_image": args.image,
        "conversation_container_startup_timeout": 240,
        "conversation_container_memory": "2g",
        "conversation_container_cpus": 1.0,
    }
    config_path = root / "agent_server_config.json"
    config_path.write_text(json.dumps(config))
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "OH_PERSISTENCE_DIR": str(persistence),
        "OH_SECRET_KEY": OUTER_KEY,
        "OPENHANDS_AGENT_SERVER_CONFIG_PATH": str(config_path),
        "OH_TELEMETRY_CONSENT": "false",
    }
    for name in ("DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT", "TMPDIR"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    server_log = open(root / "outer-agent-server.log", "w")  # noqa: SIM115
    server_cmd = [
        str(server_python),
        "-m",
        "openhands.agent_server",
        "--host",
        "127.0.0.1",
        "--port",
        str(server_port),
    ]
    summary["server_command"] = server_cmd
    server = subprocess.Popen(
        server_cmd, env=env, cwd=str(root), stdout=server_log, stderr=subprocess.STDOUT
    )
    outer = f"http://127.0.0.1:{server_port}"
    headers = {"X-Session-API-Key": SESSION_KEY}
    client = httpx.Client(base_url=outer, headers=headers, timeout=300)
    conversation_id: UUID | None = None
    cid: str | None = None
    verdict = "incomplete"
    try:

        def healthy():
            if server.poll() is not None:
                raise RuntimeError(
                    f"outer server exited early with {server.returncode}"
                )
            try:
                return client.get("/health").status_code == 200
            except httpx.HTTPError:
                return False

        wait_for(healthy, 90, what="outer /health")

        # 3. start a Docker-backed conversation through the public route
        if args.agent_llm == "acp-sentinel":
            # A regular agent whose LLM carries the ACP sentinel usage_id. Title
            # generation skips it exactly as it skips a real ACP agent's inert LLM;
            # no ACP subprocess is launched. Honest label: ACP-like, not ACP.
            agent_llm = LLM(model="acp-managed", usage_id="acp-managed")
        else:
            agent_llm = LLM(
                model="openai/synthetic-agent-model",
                base_url=base_url,
                api_key=SecretStr(AGENT_KEY),
                usage_id="agent",
            )
        request = StartConversationRequest(
            agent=Agent(llm=agent_llm, tools=[]),
            workspace=LocalWorkspace(working_dir="/workspace"),
            confirmation_policy=NeverConfirm(),
            title_llm_profile=AUX_PROFILE,
        )
        body = request.model_dump(mode="json", context={"expose_secrets": True})
        t0 = time.monotonic()
        response = client.post("/api/conversations", json=body)
        summary["start_status"] = response.status_code
        summary["start_seconds"] = round(time.monotonic() - t0, 1)
        if response.status_code >= 300:
            summary["start_body"] = response.text[:2000]
            raise RuntimeError(
                f"start_conversation failed: {response.status_code} "
                f"{response.text[:500]}"
            )
        conversation_id = UUID(response.json()["id"])
        summary["conversation_id"] = str(conversation_id)
        summary["title_llm_profile_in_info"] = response.json().get("title_llm_profile")

        cid = wait_for(
            lambda: find_container(conversations), 30, what="conversation container"
        )
        summary["container_id"] = cid
        summary["mounts"] = container_mounts(cid)
        summary["host_persistence_mounted"] = any(
            m["source"] == str(persistence) for m in summary["mounts"]
        )

        # 4. independent checks inside the container: endpoint reachability and
        #    the inner profile directory
        reach = run(
            [
                "docker",
                "exec",
                cid,
                "/agent-server/.venv/bin/python",
                "-c",
                "import urllib.request;"
                f"print(urllib.request.urlopen('{base_url}/models', timeout=10)"
                ".read().decode())",
            ],
            timeout=60,
        )
        summary["container_reaches_endpoint"] = (
            reach.returncode == 0 and "synthetic-title-model" in reach.stdout
        )
        summary["container_reach_output"] = (reach.stdout + reach.stderr).strip()[-300:]
        listing = run(
            ["docker", "exec", cid, "ls", "-la", "/var/openhands/.openhands/profiles"],
            timeout=60,
        )
        summary["container_profiles_listing"] = (
            listing.stdout + listing.stderr
        ).strip()
        runtime_profiles = (
            persistence
            / "runtime-data"
            / conversation_id.hex
            / "persistence"
            / "profiles"
        )
        summary["runtime_profile_files"] = (
            sorted(p.name for p in runtime_profiles.glob("*"))
            if runtime_profiles.exists()
            else None
        )
        summary["OH_PERSISTENCE_DIR_in_container"] = run(
            ["docker", "exec", cid, "sh", "-c", "echo $OH_PERSISTENCE_DIR"], timeout=60
        ).stdout.strip()

        # 5. first user message -> AutoTitleSubscriber -> title
        message = SendMessageRequest(content=[TextContent(text=MESSAGE)], run=False)
        sent = client.post(
            f"/api/conversations/{conversation_id}/events",
            json=message.model_dump(mode="json"),
        )
        summary["send_message_status"] = sent.status_code
        if sent.status_code >= 300:
            raise RuntimeError(
                f"send_message failed: {sent.status_code} {sent.text[:500]}"
            )
        meta_path = conversations / conversation_id.hex / "meta.json"

        def persisted_title():
            if meta_path.exists():
                try:
                    return json.loads(meta_path.read_text()).get("title")
                except json.JSONDecodeError:
                    return None
            return None

        title = wait_for(
            persisted_title, args.title_timeout, what="persisted title in meta.json"
        )
        summary["title_from_meta_json"] = title
        # the same record as read through the inner runtime's public API
        creds = client.post(
            f"/api/conversations/{conversation_id}/runtime/credentials"
        ).json()
        binding = run(["docker", "port", cid, "8000/tcp"]).stdout.strip()
        inner = httpx.get(
            f"http://{binding}/api/conversations/{conversation_id}",
            headers={"X-Session-API-Key": creds["session_api_key"]},
            timeout=30,
        )
        summary["title_from_inner_api"] = (
            inner.json().get("title")
            if inner.status_code == 200
            else f"status {inner.status_code}"
        )
        summary["endpoint_records"] = list(SyntheticEndpoint.records)
        inner_logs = run(["docker", "logs", cid], timeout=60)
        (args.evidence_dir / f"{args.label}-inner-container.log").write_text(
            inner_logs.stdout + inner_logs.stderr
        )
        summary["inner_log_title_lines"] = [
            line.strip()[:300]
            for line in (inner_logs.stdout + inner_logs.stderr).splitlines()
            if "title" in line.lower()
            and ("profile" in line.lower() or "error" in line.lower())
        ]

        # 6. at-rest checks on the runtime copy (host side, via the SDK store API)
        runtime_file = runtime_profiles / f"{AUX_PROFILE}.json"
        if runtime_file.exists():
            from openhands.agent_server.docker_runtime.provisioning import (
                RuntimeIdentity,
            )

            manifest = persistence / "runtime-control" / f"{conversation_id.hex}.json"
            identity = RuntimeIdentity.model_validate_json(
                manifest.read_text(), context={"cipher": outer_cipher}
            )
            runtime_store = LLMProfileStore(base_dir=runtime_profiles)
            with_runtime_key = runtime_store.load(AUX_PROFILE, cipher=identity.cipher)
            with_host_key = runtime_store.load(AUX_PROFILE, cipher=outer_cipher)
            summary["runtime_copy"] = {
                "plaintext_key_in_file": AUX_KEY in runtime_file.read_text(),
                "decrypts_with_runtime_key": bool(with_runtime_key.api_key)
                and with_runtime_key.api_key.get_secret_value() == AUX_KEY,
                "base_url": with_runtime_key.base_url,
                "model": with_runtime_key.model,
                "host_key_yields_secret": bool(with_host_key.api_key)
                and with_host_key.api_key.get_secret_value() == AUX_KEY,
                "provider_connection_id": with_runtime_key.provider_connection_id,
            }
        else:
            summary["runtime_copy"] = None
        summary["unrelated_profile_in_runtime"] = (
            runtime_profiles / f"{UNRELATED_PROFILE}.json"
        ).exists()
        summary["provider_connections_copied"] = (
            persistence
            / "runtime-data"
            / conversation_id.hex
            / "persistence"
            / "provider-connections"
            / "provider_connections.json"
        ).exists()

        # 6b. optional: repeated starts of the established conversation keep the
        #     snapshot: same request, host-edited profile, and a different name.
        if args.repeat_starts:
            import hashlib as _hashlib

            def runtime_state():
                files = sorted(runtime_profiles.glob("*.json"))
                return {
                    "profiles": [f.name for f in files],
                    "sha256": {
                        f.name: _hashlib.sha256(f.read_bytes()).hexdigest()
                        for f in files
                    },
                }

            before = runtime_state()
            # Pin the id: the request model defaults it to null, and the route
            # would otherwise mint a new conversation for every repeat.
            body = {**body, "conversation_id": str(conversation_id)}
            statuses = []
            statuses.append(
                (
                    "same-request",
                    client.post("/api/conversations", json=body).status_code,
                )
            )
            host_store.save(
                AUX_PROFILE,
                LLM(
                    model="openai/host-edit",
                    base_url=base_url,
                    api_key=SecretStr("rotated-" + AUX_KEY),
                    usage_id="title-aux",
                ),
                include_secrets=True,
                cipher=outer_cipher,
            )
            statuses.append(
                (
                    "after-host-edit",
                    client.post("/api/conversations", json=body).status_code,
                )
            )
            other_body = {**body, "title_llm_profile": UNRELATED_PROFILE}
            statuses.append(
                (
                    "different-profile",
                    client.post("/api/conversations", json=other_body).status_code,
                )
            )
            after = runtime_state()
            summary["repeated_starts"] = {
                "statuses": statuses,
                "profiles_before": before["profiles"],
                "profiles_after": after["profiles"],
                "snapshot_bytes_unchanged": before["sha256"] == after["sha256"],
                "unrelated_profile_imported": f"{UNRELATED_PROFILE}.json"
                in after["profiles"],
            }
            # the inner conversation is untouched by the repeats
            inner_again = httpx.get(
                f"http://{binding}/api/conversations/{conversation_id}",
                headers={"X-Session-API-Key": creds["session_api_key"]},
                timeout=30,
            )
            summary["repeated_starts"]["inner_title_after_repeats"] = (
                inner_again.json().get("title")
                if inner_again.status_code == 200
                else f"status {inner_again.status_code}"
            )

        # 7. optional: release + reprovision keeps the staged profile
        if args.restart:
            released = client.delete(f"/api/conversations/{conversation_id}/runtime")
            summary["release_status"] = released.status_code
            wait_for(
                lambda: find_container(conversations) is None,
                60,
                what="container release",
            )
            re = client.post(
                f"/api/conversations/{conversation_id}/runtime/reprovision"
            )
            summary["reprovision_status"] = re.status_code
            cid = wait_for(
                lambda: find_container(conversations),
                30,
                what="reprovisioned container",
            )
            summary["container_id_after_restart"] = cid
            relisting = run(
                ["docker", "exec", cid, "ls", "/var/openhands/.openhands/profiles"],
                timeout=60,
            )
            summary["container_profiles_after_restart"] = relisting.stdout.split()
            summary["mounts_after_restart"] = len(container_mounts(cid))

        # 8. secret bytes must not appear in runtime plaintext or captured logs
        server_log.flush()
        scan_roots = [
            persistence / "runtime-data",
            conversations,
            root / "outer-agent-server.log",
            args.evidence_dir / f"{args.label}-inner-container.log",
        ]
        summary["aux_key_plaintext_hits"] = scan_for_secret(scan_roots, AUX_KEY)
        summary["unrelated_key_plaintext_hits"] = scan_for_secret(
            scan_roots, UNRELATED_KEY
        )

        produced_by = (
            "aux"
            if title == AUX_TITLE
            else "agent"
            if title == AGENT_TITLE
            else "truncation"
            if title and MESSAGE.startswith(title.rstrip("."))
            else "other"
        )
        summary["title_produced_by"] = produced_by
        repeats_ok = True
        if args.repeat_starts:
            repeats = summary["repeated_starts"]
            repeats_ok = (
                all(status < 300 for _, status in repeats["statuses"])
                and repeats["snapshot_bytes_unchanged"]
                and repeats["profiles_after"] == repeats["profiles_before"]
                and not repeats["unrelated_profile_imported"]
            )
        verdict = "PASS" if produced_by == args.expect and repeats_ok else "FAIL"
    except Exception as exc:  # report, then clean up
        summary["error"] = f"{type(exc).__name__}: {exc}"
        verdict = "ERROR"
    finally:
        cleanup: dict = {}
        try:
            if conversation_id is not None and server.poll() is None:
                cleanup["delete_status"] = client.delete(
                    f"/api/conversations/{conversation_id}", timeout=120
                ).status_code
        except Exception as exc:
            cleanup["delete_error"] = str(exc)
        if server.poll() is None:
            server.send_signal(signal.SIGTERM)
            try:
                server.wait(30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(10)
        cleanup["server_exit"] = server.returncode
        server_log.close()
        leftovers = []
        for candidate in run(
            ["docker", "ps", "-aq", "--filter", f"label={OWNER_LABEL}"]
        ).stdout.split():
            inspect = run(["docker", "inspect", candidate])
            if inspect.returncode == 0 and str(conversations) in inspect.stdout:
                leftovers.append(candidate)
                run(["docker", "rm", "-f", candidate])
        cleanup["leftover_containers_removed"] = leftovers
        endpoint.shutdown()
        cleanup["runtime_dir_remains"] = (
            persistence
            / "runtime-data"
            / (conversation_id.hex if conversation_id else "-")
        ).exists()
        cleanup["conversation_dir_remains"] = (
            conversations / (conversation_id.hex if conversation_id else "-")
        ).exists()
        shutil.copy(
            root / "outer-agent-server.log",
            args.evidence_dir / f"{args.label}-outer-agent-server.log",
        )
        if args.keep:
            cleanup["kept_root"] = str(root)
        else:
            shutil.rmtree(root, ignore_errors=True)
        summary["cleanup"] = cleanup
        summary["verdict"] = verdict
        summary["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (args.evidence_dir / f"{args.label}-summary.json").write_text(
            json.dumps(summary, indent=2, default=str)
        )
        print(json.dumps(summary, indent=2, default=str))
        print(
            f"VERDICT {args.label}: {verdict} (expected title from {args.expect}, "
            f"got {summary.get('title_produced_by')}: "
            f"{summary.get('title_from_meta_json')!r})"
        )
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
