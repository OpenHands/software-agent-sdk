"""An automation script's observability plumbing: the runner exports the
generic OPENHANDS_OBSERVABILITY_* variables, the script turns them into
`Conversation(...)` kwargs and into headers for direct agent-server calls, and
a remote workspace pointed at a local capture server sends those headers.
Also: reading the env from exec'd code in a thread, and what a `.env` file in
the working directory does.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from sdk_verify import emit, make_llm, run_paths


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "S18"))
from servers import CaptureServer  # noqa: E402

from openhands.sdk import Agent, Conversation  # noqa: E402
from openhands.sdk.automation import (  # noqa: E402
    automation_conversation_kwargs,
    automation_conversation_tags,
    automation_observability_context,
    automation_observability_headers,
)
from openhands.sdk.workspace import RemoteWorkspace  # noqa: E402


os.environ.update(
    {
        "OPENHANDS_OBSERVABILITY_METADATA": json.dumps(
            {
                "automation.id": "auto-1",
                "automation.name": "Nightly",
                "automation.run_id": "run-5",
                "automation.trigger_source": "cron",
            }
        ),
        "OPENHANDS_OBSERVABILITY_TAGS": "nightly, verify,nightly",
        "OPENHANDS_OBSERVABILITY_SPAN_NAME": "automation-run",
        "OPENHANDS_OBSERVABILITY_PARENT_SPAN_CONTEXT": "parent-ctx-token",
    }
)
context = automation_observability_context(metadata={"extra": 1}, tags=["mine"])
kwargs = automation_conversation_kwargs(conversation_tags={"team": "infra"})
explicit = automation_conversation_kwargs(span_name="explicit-span")
headers = automation_observability_headers()

# The kwargs go straight into a conversation (a scripted one here).
conversation = Conversation(
    agent=Agent(llm=make_llm([{"finish": "ok"}]), tools=[]),
    workspace=str(run_paths()["workspace"]),
    visualizer=None,
    **kwargs,
)
conversation.send_message("hi")
conversation.run()
conversation_tags = dict(conversation.state.tags or {})
conversation.close()

# A remote workspace sends the same headers to the server it talks to, and
# with its completion callback.
with CaptureServer() as server:
    os.environ["AUTOMATION_CALLBACK_URL"] = f"{server.base_url}/callback"
    with RemoteWorkspace(host=server.base_url, working_dir="/workspace") as ws:
        ws.execute_command("true", timeout=2)
    os.environ.pop("AUTOMATION_CALLBACK_URL")
    sent = {r["path"].split("?")[0]: r["headers"] for r in server.requests}

start = sent.get("/api/bash/start_bash_command", {})
callback = sent.get("/callback", {})

# A .env file in the working directory: the SDK does not read it, but
# importing the SDK imports LiteLLM, which calls load_dotenv() on it.
dotenv_dir = run_paths()["run"] / "dotenv-case"
dotenv_dir.mkdir(exist_ok=True)
(dotenv_dir / ".env").write_text("OTEL_ENDPOINT=http://127.0.0.1:9/v1/traces\n")
child_env = {
    k: v
    for k, v in os.environ.items()
    if not k.startswith(("OTEL_", "LMNR_", "OPENHANDS_OBSERVABILITY_"))
}
probe = subprocess.run(
    [
        sys.executable,
        "-c",
        "from openhands.sdk.observability.laminar import "
        "should_enable_observability as s; print(s())",
    ],
    cwd=dotenv_dir,
    env=child_env,
    capture_output=True,
    text=True,
    timeout=120,
)
# Code exec'd in a thread (no file-backed frames) can still read the
# observability env: get_env never walks the call stack like find_dotenv().
import threading  # noqa: E402


exec_result: dict = {}


def in_thread() -> None:
    try:
        scope: dict = {}
        exec(
            "from openhands.sdk.observability.utils import get_env\n"
            "from openhands.sdk.observability.laminar import "
            "observability_tags_from_env\n"
            "value = (get_env('OPENHANDS_OBSERVABILITY_SPAN_NAME'), "
            "observability_tags_from_env())",
            scope,
        )
        exec_result["value"] = scope["value"]
    except BaseException as exc:  # an AssertionError here is the old bug
        exec_result["error"] = f"{type(exc).__name__}: {exc}"


t = threading.Thread(target=in_thread)
t.start()
t.join()
gate = {}
for key in (
    "OTEL_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
):
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "from openhands.sdk.observability.laminar import "
            "should_enable_observability as s; print(s())",
        ],
        env={**child_env, key: "http://127.0.0.1:9/v1/traces"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    gate[key] = (
        r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]
    )

emit(
    {
        "ok": True,
        "context": context,
        "kwargs": kwargs,
        "explicit_span_name": explicit.get("observability_span_name"),
        "tags_from_metadata": automation_conversation_tags(),
        "headers": {k: v for k, v in headers.items()},
        "conversation_tags": conversation_tags,
        "workspace_headers": {
            k: v for k, v in start.items() if k.startswith("x-openhands-observability")
        },
        "callback_headers": {
            k: v
            for k, v in callback.items()
            if k.startswith("x-openhands-observability")
        },
        "exec_thread": exec_result,
        "dotenv_enables": probe.stdout.strip().splitlines()[-1]
        if probe.stdout.strip()
        else probe.stderr[-200:],
        "gate": gate,
    }
)
