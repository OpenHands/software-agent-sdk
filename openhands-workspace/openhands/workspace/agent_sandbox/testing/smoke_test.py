"""Keyless smoke test for AgentSandboxWorkspace against a real cluster (no LLM).

Validates the integration end to end without needing an LLM: claim a pod from the
warm pool, run a command inside it, pause/resume, and confirm the workspace state
survives the suspend. Works against the secure default template (no network policy
changes needed) because it never leaves the pod to reach an LLM.

Prerequisites (see TESTING.md): a cluster reachable via your kubeconfig with the
agent-sandbox controller installed and a warm pool of agent-server pods. Then:

    pip install openhands-workspace[agent-sandbox]
    python smoke_test.py

Env (optional):
    AGENT_SANDBOX_WARMPOOL   warm pool name   (default: openhands-pool)
    AGENT_SANDBOX_NAMESPACE  namespace        (default: default)
"""

import os
import time

from openhands.workspace import AgentSandboxWorkspace


WARMPOOL = os.environ.get("AGENT_SANDBOX_WARMPOOL", "openhands-pool")
NAMESPACE = os.environ.get("AGENT_SANDBOX_NAMESPACE", "default")


def main() -> None:
    t0 = time.time()
    with AgentSandboxWorkspace(warmpool=WARMPOOL, namespace=NAMESPACE) as ws:
        print(f"claimed + connected in {time.time() - t0:.1f}s at {ws.host}")

        result = ws.execute_command("echo hello-from-pod && whoami && pwd && uname -m")
        assert result.exit_code == 0, result
        print("command output:\n" + result.stdout)

        # Write a file, then suspend and resume the sandbox.
        ws.execute_command("echo persisted-across-suspend > /workspace/marker.txt")
        print("pause() -> Suspended ...")
        ws.pause()
        time.sleep(3)
        print("resume() -> Running ...")
        ws.resume()

        # With a persistent volume the file is still there after the resume.
        out = ws.execute_command("cat /workspace/marker.txt")
        assert out.stdout.strip() == "persisted-across-suspend", out
        print(f"state survived suspend/resume: {out.stdout!r}")

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
