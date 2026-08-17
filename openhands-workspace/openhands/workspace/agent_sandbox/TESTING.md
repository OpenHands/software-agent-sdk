# Testing `AgentSandboxWorkspace`

Three tiers, from no-cluster to a full agent run — all runnable locally with no API
key. The steps are cluster-agnostic: they work on kind, minikube, or any cluster your
`kubectl` can reach.

1. **Unit tests** — no cluster.
2. **Keyless workspace smoke test** — a real cluster, no LLM. Proves the integration
   (claim, exec, pause/resume, persistence) on its own.
3. **Full agent e2e** — a real cluster + a local Ollama model (no key), or a hosted
   LLM key.

## Prerequisites

- A Kubernetes cluster (kind/minikube/cloud) reachable via your kubeconfig, with the
  [agent-sandbox controller + extensions](https://github.com/kubernetes-sigs/agent-sandbox#installation)
  installed.
- `kubectl`, and for a local Ollama run, [`ollama`](https://ollama.com) (or use the
  in-cluster Ollama manifest below).
- The package with its optional client:

  ```bash
  pip install openhands-workspace[agent-sandbox]
  ```

## 1. Unit tests (no cluster)

```bash
uv run pytest tests/workspace/test_agent_sandbox_workspace.py -v
```

These mock the `k8s-agent-sandbox` client, so no cluster or credentials are needed.

## 2. Cluster setup (shared by tiers 2 and 3)

Install the controller (pick a release from
<https://github.com/kubernetes-sigs/agent-sandbox/releases>):

```bash
export VERSION="v0.5.2"
kubectl apply -f "https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/sandbox-with-extensions.yaml"
kubectl -n agent-sandbox-system rollout status deploy --timeout=180s
```

Apply the agent-server `SandboxTemplate` and `SandboxWarmPool` (from
[`../deploy/`](../deploy/)):

```bash
kubectl apply -f ../deploy/sandboxtemplate.yaml
kubectl apply -f ../deploy/sandboxwarmpool.yaml
kubectl get pods -w    # wait for the warm pool pods to become Ready
```

The first pull of `ghcr.io/openhands/agent-server` can take a minute. On kind you can
pre-load it to avoid an in-cluster pull:

```bash
docker pull ghcr.io/openhands/agent-server:1.42.1-python
kind load docker-image ghcr.io/openhands/agent-server:1.42.1-python --name <cluster>
```

## 3. Keyless workspace smoke test (no LLM)

This validates the whole integration without an LLM and works against the **secure
default template** — it never leaves the pod, so no network-policy change is needed.

```bash
python testing/smoke_test.py
```

Expected: a sub-second claim from the warm pool, a command run in the pod, then a
`pause()` / `resume()` where a file written before the pause is still present after
(persistent-volume state survives the suspend), and a clean teardown.

## 4. Full agent e2e

An OpenHands agent runs *inside* the sandbox pod and calls the LLM from there, so the
**sandbox pod must be able to reach the LLM**:

- **Hosted LLM (public API):** the secure default template already allows public
  egress — nothing to change.
- **Local / in-cluster LLM:** the default template's `NetworkPolicy` blocks private
  ranges (cluster and host IPs). For a test, relax it:

  ```bash
  kubectl patch sandboxtemplate openhands-agent-server --type merge \
    -p '{"spec":{"networkPolicyManagement":"Unmanaged"}}'
  # recreate the pool so new pods pick up the change:
  kubectl patch sandboxwarmpool openhands-pool --type merge -p '{"spec":{"replicas":0}}'
  kubectl patch sandboxwarmpool openhands-pool --type merge -p '{"spec":{"replicas":1}}'
  ```

  `Unmanaged` drops the policy entirely, which is fine for a throwaway test cluster
  but not for real use. For anything beyond a local test, keep the policy **Managed**
  and allow only what the agent needs — see the commented `networkPolicy` block in
  [`deploy/sandboxtemplate.yaml`](deploy/sandboxtemplate.yaml) for a copy-pasteable
  scoped-egress rule (DNS + your LLM endpoint).

### 4a. No key — local Ollama

Deploy the in-cluster Ollama and pull a tool-capable model:

```bash
kubectl apply -f testing/ollama.yaml
kubectl rollout status deploy/ollama --timeout=180s
kubectl exec deploy/ollama -- ollama pull qwen2.5
```

> **Model choice matters.** Use a model that returns *structured* tool calls:
> `qwen2.5` (7b) and `llama3.1:8b` work. `qwen2.5-coder` and the `:3b` variants
> return tool calls as plain **text**, so the agent never acts. Quick check:
>
> ```bash
> curl -s http://<ollama>:11434/api/chat -d '{"model":"qwen2.5","stream":false,
>   "messages":[{"role":"user","content":"call run_bash to make hello.txt"}],
>   "tools":[{"type":"function","function":{"name":"run_bash","parameters":
>   {"type":"object","properties":{"command":{"type":"string"}}}}}]}' | python3 -c \
>   'import sys,json;print("tool_calls:",json.load(sys.stdin)["message"].get("tool_calls"))'
> ```
>
> A non-`null` `tool_calls` means the model is usable.

Run it, pointing at the Ollama Service:

```bash
export OLLAMA_URL="http://$(kubectl get svc ollama -o jsonpath='{.spec.clusterIP}'):11434"
python testing/agent_ollama_example.py
```

Notes baked into the example:
- `reasoning_effort="none"` — qwen2.5 has no "thinking" mode (otherwise the request
  is rejected with `does not support thinking`).
- A **minimal terminal-only agent** — the default multi-tool agent overwhelms models
  this size; they plan/think/finish without executing.
- Small models on CPU are slow (up to minutes per turn); the example sets a generous
  timeout.

### 4b. With a hosted key

Any `RemoteWorkspace`-style usage works; construct the workspace and hand it to a
`Conversation`:

```python
from pydantic import SecretStr
from openhands.sdk import LLM, Conversation
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import AgentSandboxWorkspace

llm = LLM(usage_id="agent", model="<model>", api_key=SecretStr("<key>"))
with AgentSandboxWorkspace(warmpool="openhands-pool") as ws:
    conv = Conversation(agent=get_default_agent(llm=llm), workspace=ws)
    conv.send_message("Write 3 facts about this repo into FACTS.txt.")
    conv.run()
```

A capable hosted model can drive the full default agent; that's why 4b uses
`get_default_agent` while 4a uses the minimal agent.

## What success looks like

- A `SandboxClaim`, `Sandbox`, and pod appear while a test runs:

  ```bash
  kubectl get sandboxclaim,sandbox,pods
  ```

- Smoke test: the marker file survives `pause()`/`resume()`.
- Agent e2e: the agent emits a `terminal` action, and `/workspace/hello.txt` ends up
  with the expected content.
- On exit the workspace deletes the `SandboxClaim` (and its pod) automatically.

## Cleanup

```bash
kubectl delete -f testing/ollama.yaml --ignore-not-found
kubectl delete -f ../deploy/sandboxwarmpool.yaml -f ../deploy/sandboxtemplate.yaml --ignore-not-found
# kind: kind delete cluster --name <cluster>
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Agent connects but the LLM call times out; nothing reaches Ollama | The sandbox pod can't reach the LLM. The default `NetworkPolicy` blocks private ranges (cluster ClusterIPs, host IPs). Add a scoped egress rule (see the commented block in `deploy/sandboxtemplate.yaml`), or `networkPolicyManagement: Unmanaged` for a throwaway test cluster. Public/hosted APIs are already allowed. |
| Agent replies with text and `finish`es without doing anything (no `ActionEvent`) | The model returns tool calls as text. Use `qwen2.5` (7b) or `llama3.1:8b`; avoid `qwen2.5-coder` and `:3b`. Verify with the raw `/api/chat` probe above. |
| `litellm ... "<model>" does not support thinking` | Set `reasoning_effort="none"` on the `LLM` (qwen2.5 has no thinking mode). |
| Agent plans/thinks/finishes but never runs the command | The default 7-tool agent is too heavy for a small model. Use a minimal terminal-only agent (as in `agent_ollama_example.py`), or a larger/hosted model. |
| `model '<name>' not found` after editing the Ollama Deployment | The model store is an `emptyDir`; editing the Deployment restarts the pod and wipes it. `kubectl exec deploy/ollama -- ollama pull <model>` again (or use a PVC). |
| `resume()` fails with a port or "network namespace is closed" error | Transient churn right after resume; the workspace retries with a fresh local port. If you see it persist, raise `health_check_timeout`. |
| Claim never becomes Ready | First agent-server image pull is slow; pre-load it (see step 2) or raise `sandbox_ready_timeout`. |
| `AgentSandboxWorkspace requires the 'agent-sandbox' extra` | `pip install openhands-workspace[agent-sandbox]`. |
