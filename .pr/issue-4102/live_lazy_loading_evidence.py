#!/usr/bin/env python3
"""Live before/after evidence for issue #4102.

This harness generates a deterministic, production-format persisted conversation
corpus and launches the real Agent Server against exact source checkouts.  It
uses the production owner lease files and "Resumed conversation" log entries as
external runtime-hydration signals; no test-only endpoint or monkeypatch is used.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5


MAIN_SHA = "777671766f4196da318b5b1e6179a6dc897cad36"
PR_SHA = "bf0c3d3f70a39c1fe2c15ec2ecd5852260b9ce48"
DEFAULT_CONVERSATIONS = 120
DEFAULT_EVENTS = 460


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _corpus_models():
    # Imports are deliberately deferred so `benchmark` can launch each exact
    # checkout without importing the PR's models into the orchestrator process.
    from openhands.agent_server.models import StoredConversation
    from openhands.sdk import LLM, Agent, Message
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.event.llm_convertible import MessageEvent
    from openhands.sdk.llm import TextContent
    from openhands.sdk.security.confirmation_policy import NeverConfirm
    from openhands.sdk.workspace import LocalWorkspace

    return {
        "Agent": Agent,
        "ConversationState": ConversationState,
        "LLM": LLM,
        "LocalWorkspace": LocalWorkspace,
        "Message": Message,
        "MessageEvent": MessageEvent,
        "NeverConfirm": NeverConfirm,
        "StoredConversation": StoredConversation,
        "TextContent": TextContent,
    }


def generate_corpus(root: Path, conversations: int, events: int) -> dict[str, Any]:
    models = _corpus_models()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    workspace_dir = root.parent / "shared-workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    agent = models["Agent"](
        llm=models["LLM"](model="gpt-4o", usage_id="issue-4102-live-evidence"),
        tools=[],
    )
    workspace = models["LocalWorkspace"](working_dir=str(workspace_dir))
    confirmation_policy = models["NeverConfirm"]()
    created_at = datetime(2026, 7, 13, 0, 0, tzinfo=UTC)
    digest = hashlib.sha256()
    conversation_ids: list[str] = []

    for conversation_index in range(conversations):
        conversation_id = uuid5(
            NAMESPACE_URL, f"openhands-issue-4102-conversation-{conversation_index}"
        )
        conversation_ids.append(str(conversation_id))
        conversation_dir = root / conversation_id.hex
        events_dir = conversation_dir / "events"
        events_dir.mkdir(parents=True)

        stored = models["StoredConversation"](
            id=conversation_id,
            agent=agent,
            workspace=workspace,
            confirmation_policy=confirmation_policy,
            initial_message=None,
            title=f"Issue 4102 corpus conversation {conversation_index:03d}",
            created_at=created_at + timedelta(seconds=conversation_index),
            updated_at=created_at + timedelta(seconds=conversation_index),
        )
        state = models["ConversationState"](
            id=conversation_id,
            agent=agent,
            workspace=workspace,
            persistence_dir=str(conversation_dir),
            confirmation_policy=confirmation_policy,
        )
        meta_payload = stored.model_dump_json(exclude_none=True)
        state_payload = state.model_dump_json(exclude_none=True)
        (conversation_dir / "meta.json").write_text(meta_payload)
        (conversation_dir / "base_state.json").write_text(state_payload)
        digest.update(meta_payload.encode())
        digest.update(state_payload.encode())

        for event_index in range(events):
            event_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"openhands-issue-4102-event-{conversation_index}-{event_index}",
                )
            )
            is_user = event_index % 2 == 0
            event = models["MessageEvent"](
                id=event_id,
                timestamp=(
                    created_at
                    + timedelta(seconds=conversation_index, microseconds=event_index)
                ).isoformat(),
                source="user" if is_user else "agent",
                llm_message=models["Message"](
                    role="user" if is_user else "assistant",
                    content=[
                        models["TextContent"](
                            text=(
                                "Deterministic live evidence message "
                                f"{conversation_index:03d}/{event_index:03d}"
                            )
                        )
                    ],
                ),
            )
            payload = event.model_dump_json(exclude_none=True)
            event_path = events_dir / f"event-{event_index:05d}-{event_id}.json"
            event_path.write_text(payload)
            digest.update(payload.encode())

    manifest = {
        "conversation_count": conversations,
        "events_per_conversation": events,
        "event_file_count": conversations * events,
        "conversation_ids": conversation_ids,
        "content_sha256": digest.hexdigest(),
        "generator_python": sys.version,
    }
    _json_dump(root.parent / "corpus_manifest.json", manifest)
    return manifest


def _source_pythonpath(source: Path) -> str:
    return os.pathsep.join(
        str(source / part)
        for part in (
            "openhands-agent-server",
            "openhands-sdk",
            "openhands-tools",
            "openhands-workspace",
        )
    )


def _http_json(
    url: str, *, method: str = "GET", body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-Session-API-Key": "issue-4102-local-evidence",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload


def _wait_ready(base_url: str, process: subprocess.Popen[str]) -> float:
    started = time.monotonic()
    deadline = started + 300
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Agent Server exited during startup: {process.returncode}"
            )
        try:
            status, payload = _http_json(f"{base_url}/ready")
            if status == 200 and payload.get("status") == "ready":
                return time.monotonic() - started
        except (OSError, TimeoutError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise TimeoutError("Agent Server did not become ready within 300 seconds")


def _proc_memory_kib(pid: int) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(("VmRSS:", "VmHWM:")):
            key, raw = line.split(":", 1)
            values[key] = int(raw.split()[0])
    return values


def _lease_ids(conversations_dir: Path) -> list[str]:
    return sorted(
        path.parent.name
        for path in conversations_dir.glob("*/owner_lease.json")
    )


def _resume_count(log_path: Path, conversation_id: str | None = None) -> int:
    text = log_path.read_text(errors="replace") if log_path.exists() else ""
    marker = "Resumed conversation"
    if conversation_id is None:
        return sum(marker in line for line in text.splitlines())
    return sum(marker in line and conversation_id in line for line in text.splitlines())


def _copy_corpus(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["cp", "-a", "--reflink=auto", str(source), str(destination)], check=True
    )


def _launch_server(
    *,
    source: Path,
    python: Path,
    conversations_dir: Path,
    run_dir: Path,
    port: int,
    sha: str,
) -> tuple[subprocess.Popen[str], Any, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "agent-server.log"
    log_handle = log_path.open("w")
    env = os.environ.copy()
    # Make authentication deterministic even when the caller's shell has a
    # legacy SESSION_API_KEY set for unrelated local work.
    env.pop("SESSION_API_KEY", None)
    env.update(
        {
            "PYTHONPATH": _source_pythonpath(source),
            "OH_CONVERSATIONS_PATH": str(conversations_dir),
            "OH_WORKSPACE_PATH": str(run_dir / "default-workspace"),
            "OH_BASH_EVENTS_DIR": str(run_dir / "bash-events"),
            "OH_ENABLE_VSCODE": "false",
            "OH_ENABLE_VNC": "false",
            "OH_PRELOAD_TOOLS": "false",
            "OH_LEASE_TTL_SECONDS": "600",
            "OH_SESSION_API_KEYS_0": "issue-4102-local-evidence",
            "OPENHANDS_BUILD_GIT_SHA": sha,
            "OPENHANDS_SUPPRESS_BANNER": "1",
            "LOG_LEVEL": "INFO",
        }
    )
    process = subprocess.Popen(
        [
            str(python),
            "-m",
            "openhands.agent_server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=source,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, log_handle, log_path


def _stop_server(process: subprocess.Popen[str], log_handle: Any) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    log_handle.close()


def probe_server(
    *,
    label: str,
    source: Path,
    python: Path,
    corpus: Path,
    run_root: Path,
    port: int,
    sha: str,
    expected_conversations: int,
    expected_events: int,
) -> dict[str, Any]:
    run_dir = run_root / label
    conversations_dir = run_dir / "conversations"
    _copy_corpus(corpus, conversations_dir)
    process, log_handle, log_path = _launch_server(
        source=source,
        python=python,
        conversations_dir=conversations_dir,
        run_dir=run_dir,
        port=port,
        sha=sha,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        startup_seconds = _wait_ready(base_url, process)
        time.sleep(0.5)
        startup_memory = _proc_memory_kib(process.pid)
        startup_leases = _lease_ids(conversations_dir)
        startup_resume_logs = _resume_count(log_path)

        count_status, count_payload = _http_json(f"{base_url}/api/conversations/count")
        search_status, search_payload = _http_json(
            f"{base_url}/api/conversations/search?limit=100"
        )
        assert count_status == 200 and count_payload == expected_conversations
        assert search_status == 200 and len(search_payload["items"]) == 100
        after_catalog_leases = _lease_ids(conversations_dir)

        ids = json.loads((run_root / "corpus_manifest.json").read_text())[
            "conversation_ids"
        ]
        hydrate_id = ids[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(
                executor.map(
                    lambda _: _http_json(
                        f"{base_url}/api/conversations/{hydrate_id}/events/count"
                    ),
                    range(8),
                )
            )
        assert all(
            status == 200 and payload == expected_events
            for status, payload in responses
        )
        time.sleep(0.5)
        after_hydration_leases = _lease_ids(conversations_dir)
        target_resume_logs = _resume_count(log_path, hydrate_id)
        after_hydration_memory = _proc_memory_kib(process.pid)

        duplicate_source_id = ids[1]
        duplicate_target_id = ids[2]
        target_meta = conversations_dir / UUID(duplicate_target_id).hex / "meta.json"
        target_meta_before = hashlib.sha256(target_meta.read_bytes()).hexdigest()
        fork_status, fork_payload = _http_json(
            f"{base_url}/api/conversations/{duplicate_source_id}/fork",
            method="POST",
            body={"id": duplicate_target_id},
        )
        target_meta_after = hashlib.sha256(target_meta.read_bytes()).hexdigest()

        return {
            "label": label,
            "sha": sha,
            "pid": process.pid,
            "startup_seconds": round(startup_seconds, 3),
            "startup_memory_kib": startup_memory,
            "startup_runtime_count": len(startup_leases),
            "startup_resume_log_count": startup_resume_logs,
            "catalog_count_http": {"status": count_status, "body": count_payload},
            "catalog_search_http": {
                "status": search_status,
                "items": len(search_payload["items"]),
                "next_page_id_present": bool(search_payload.get("next_page_id")),
            },
            "runtime_count_after_catalog_reads": len(after_catalog_leases),
            "concurrent_event_count_requests": len(responses),
            "concurrent_event_count_unique_results": sorted(
                {payload for _, payload in responses}
            ),
            "hydrated_conversation_id": hydrate_id,
            "runtime_count_after_one_conversation_access": len(
                after_hydration_leases
            ),
            "hydrated_target_resume_log_count": target_resume_logs,
            "memory_after_one_conversation_access_kib": after_hydration_memory,
            "duplicate_fork": {
                "source_id": duplicate_source_id,
                "existing_target_id": duplicate_target_id,
                "http_status": fork_status,
                "body": fork_payload,
                "target_meta_sha256_before": target_meta_before,
                "target_meta_sha256_after": target_meta_after,
                "target_unchanged": target_meta_before == target_meta_after,
            },
        }
    finally:
        _stop_server(process, log_handle)


def probe_running_recovery(
    *,
    source: Path,
    python: Path,
    corpus: Path,
    run_root: Path,
    port: int,
    sha: str,
    expected_conversations: int,
) -> dict[str, Any]:
    label = "pr-running-recovery"
    run_dir = run_root / label
    conversations_dir = run_dir / "conversations"
    _copy_corpus(corpus, conversations_dir)
    ids = json.loads((run_root / "corpus_manifest.json").read_text())[
        "conversation_ids"
    ]
    running_id = ids[-1]
    base_state_path = conversations_dir / UUID(running_id).hex / "base_state.json"
    state = json.loads(base_state_path.read_text())
    state["execution_status"] = "running"
    base_state_path.write_text(json.dumps(state))

    process, log_handle, log_path = _launch_server(
        source=source,
        python=python,
        conversations_dir=conversations_dir,
        run_dir=run_dir,
        port=port,
        sha=sha,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        startup_seconds = _wait_ready(base_url, process)
        time.sleep(0.5)
        leases = _lease_ids(conversations_dir)
        error_status, error_count = _http_json(
            f"{base_url}/api/conversations/count?status=error"
        )
        total_status, total_count = _http_json(
            f"{base_url}/api/conversations/count"
        )
        return {
            "label": label,
            "sha": sha,
            "startup_seconds": round(startup_seconds, 3),
            "running_conversation_id": running_id,
            "startup_runtime_ids": leases,
            "startup_runtime_count": len(leases),
            "running_target_resume_log_count": _resume_count(log_path, running_id),
            "error_count_http": {"status": error_status, "body": error_count},
            "total_count_http": {"status": total_status, "body": total_count},
            "expected_total": expected_conversations,
        }
    finally:
        _stop_server(process, log_handle)


def benchmark(args: argparse.Namespace) -> None:
    run_root = args.output.resolve()
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True)
    corpus_manifest = json.loads(args.manifest.read_text())
    shutil.copy2(args.manifest, run_root / "corpus_manifest.json")

    results = {
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "command": "live_lazy_loading_evidence.py benchmark",
        },
        "refs": {"current_main": MAIN_SHA, "pull_request": PR_SHA},
        "corpus": corpus_manifest,
        "before_current_main": probe_server(
            label="before-current-main",
            source=args.main_source.resolve(),
            # Do not resolve the venv's python symlink: resolving it would bypass
            # the venv and lose installed Agent Server dependencies.
            python=args.python.absolute(),
            corpus=args.corpus.resolve(),
            run_root=run_root,
            port=args.main_port,
            sha=MAIN_SHA,
            expected_conversations=corpus_manifest["conversation_count"],
            expected_events=corpus_manifest["events_per_conversation"],
        ),
        "after_pull_request": probe_server(
            label="after-pull-request",
            source=args.pr_source.resolve(),
            python=args.python.absolute(),
            corpus=args.corpus.resolve(),
            run_root=run_root,
            port=args.pr_port,
            sha=PR_SHA,
            expected_conversations=corpus_manifest["conversation_count"],
            expected_events=corpus_manifest["events_per_conversation"],
        ),
    }
    results["after_running_recovery"] = probe_running_recovery(
        source=args.pr_source.resolve(),
        python=args.python.absolute(),
        corpus=args.corpus.resolve(),
        run_root=run_root,
        port=args.recovery_port,
        sha=PR_SHA,
        expected_conversations=corpus_manifest["conversation_count"],
    )
    _json_dump(run_root / "live_results.json", results)
    print(json.dumps(results, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate")
    generate.add_argument("--root", type=Path, required=True)
    generate.add_argument("--conversations", type=int, default=DEFAULT_CONVERSATIONS)
    generate.add_argument("--events", type=int, default=DEFAULT_EVENTS)

    bench = subparsers.add_parser("benchmark")
    bench.add_argument("--main-source", type=Path, required=True)
    bench.add_argument("--pr-source", type=Path, required=True)
    bench.add_argument("--python", type=Path, required=True)
    bench.add_argument("--corpus", type=Path, required=True)
    bench.add_argument("--manifest", type=Path, required=True)
    bench.add_argument("--output", type=Path, required=True)
    bench.add_argument("--main-port", type=int, default=18100)
    bench.add_argument("--pr-port", type=int, default=18101)
    bench.add_argument("--recovery-port", type=int, default=18102)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "generate":
        print(
            json.dumps(
                generate_corpus(args.root.resolve(), args.conversations, args.events),
                indent=2,
                sort_keys=True,
            )
        )
    else:
        benchmark(args)


if __name__ == "__main__":
    main()
