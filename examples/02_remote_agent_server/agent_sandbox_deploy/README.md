# Running the agent server in agent-sandbox on a local kind cluster

End-to-end walkthrough for
[`17_convo_with_agent_sandbox_server.py`](../17_convo_with_agent_sandbox_server.py)
using [kind](https://kind.sigs.k8s.io/). The same steps work on minikube or a cloud
cluster such as GKE. Only the cluster-creation step differs.

## 1. Create a cluster

```bash
kind create cluster --name openhands
```

## 2. Install the agent-sandbox controller + extensions

Pick a release from
<https://github.com/kubernetes-sigs/agent-sandbox/releases> and apply the core +
extensions manifests:

```bash
export VERSION="vX.Y.Z"
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/manifest.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/extensions.yaml
```

Wait for the controller to be ready:

```bash
kubectl -n agent-sandbox-system rollout status deploy --timeout=120s
```

## 3. Apply the agent-server template + warm pool

```bash
kubectl apply -f sandboxtemplate.yaml
kubectl apply -f sandboxwarmpool.yaml
```

The first pull of `ghcr.io/openhands/agent-server` can take a minute. Watch the pool
fill up (pods become `Ready` once the agent server passes its `/health` probe):

```bash
kubectl get pods -w
```

## 4. Install the client and run the example

```bash
pip install openhands-workspace[agent-sandbox]

export LLM_API_KEY=...            # your LLM key (called from inside the pod)
export LLM_MODEL=...              # optional, e.g. a hosted model id
python ../17_convo_with_agent_sandbox_server.py
```

You should see: a sub-second claim from the warm pool, a command run in the pod, the
agent editing files, then a **pause** (pod suspended, PVC retained) and **resume**
(same conversation continues).

## Notes

- **LLM egress.** The agent runs on the agent server *inside* the pod, so the LLM API
  is called from the pod. kind allows outbound internet by default. To use a local
  Ollama, point `LLM_BASE_URL` at an address reachable from the pod and (on kind)
  ensure the container network can reach your host.
- **Cold start vs warm pool.** With `replicas: 0` the pool becomes on-demand and each
  claim creates a fresh pod (full cold start). Increase `replicas` to keep ready pods.
- **Strong isolation.** Uncomment `runtimeClassName: gvisor` in `sandboxtemplate.yaml`
  (requires a gVisor-enabled node pool) to sandbox untrusted agent code at the kernel
  boundary.
- **Cleanup.** `kind delete cluster --name openhands`.
