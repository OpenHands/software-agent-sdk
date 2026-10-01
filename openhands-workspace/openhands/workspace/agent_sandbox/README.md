# AgentSandboxWorkspace

Run the OpenHands agent server inside a Kubernetes pod managed by
[`kubernetes-sigs/agent-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox).

The pod is claimed from a `SandboxWarmPool`, so a pre-warmed pool gives sub-second
starts instead of paying a container cold-start per conversation. It adds native
pause/resume (via the Sandbox `operatingMode`) and, with a persistent volume,
workspace state that survives a suspend. gVisor / Kata isolation is available by
setting a `runtimeClass` on the `SandboxTemplate`.

It is a drop-in `RemoteWorkspace`, so it works anywhere a `DockerWorkspace` does,
from a laptop kind or minikube cluster to a cloud cluster such as GKE.

## Install

```bash
pip install openhands-workspace[agent-sandbox]
```

This pulls in the [`k8s-agent-sandbox`](https://pypi.org/project/k8s-agent-sandbox/)
client used to create and manage the sandbox.

## Prerequisites

1. A Kubernetes cluster reachable via your kubeconfig, with the agent-sandbox
   controller + extensions installed
   ([install guide](https://github.com/kubernetes-sigs/agent-sandbox#installation)).
2. A `SandboxTemplate` running the agent server, and a `SandboxWarmPool` that
   references it. Ready-to-apply manifests live in [`deploy/`](deploy/):

   ```bash
   kubectl apply -f deploy/sandboxtemplate.yaml
   kubectl apply -f deploy/sandboxwarmpool.yaml
   ```

## Usage

```python
from openhands.sdk import Conversation
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import AgentSandboxWorkspace

with AgentSandboxWorkspace(warmpool="openhands-pool", namespace="default") as workspace:
    result = workspace.execute_command("echo hello && pwd")
    print(result.stdout)

    conversation = Conversation(agent=get_default_agent(llm=llm), workspace=workspace)
    conversation.send_message("Write 3 facts about this repo into FACTS.txt.")
    conversation.run()
```

### Connection modes

* `connection="port_forward"` (default) spawns `kubectl port-forward` to the pod.
  This works from a laptop against local kind or minikube, and against any cluster
  your kubeconfig can reach. `host_port` picks a free local port for you.
* `connection="direct"` means you supply `host` yourself: a `sandbox-router` or
  Gateway URL, or the in-cluster DNS name when OpenHands runs in the cluster. No
  port-forward is started.

### Testing

See [`TESTING.md`](TESTING.md) for an end-to-end walkthrough: unit tests, a keyless
workspace smoke test, and a full agent run against a local Ollama model (no API key).

### Pause / resume

```python
workspace.pause()    # operatingMode -> Suspended; pod terminated, PVC retained
workspace.resume()   # operatingMode -> Running; reconnects to the agent server
```

### Where configuration lives

Most knobs belong in Kubernetes, not on the Python constructor:

- CPU, memory, image, `runtimeClass` and the security context belong in
  `SandboxTemplate.spec.podTemplate`.
- Pre-warming and pool size belong in `SandboxWarmPool.spec.replicas`.
- Network egress allow-listing belongs in the template's `NetworkPolicy`.
- Persistent volumes belong in the template's `volumeClaimTemplates`.

Python-side knobs: `warmpool` (required), `namespace`, `server_port`, `connection`,
`host_port`, `kube_context`, `sandbox_ready_timeout`, `health_check_timeout`,
`shutdown_after_seconds`, `labels`, `pod_labels`, `pod_annotations`.
