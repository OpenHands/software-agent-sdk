# Live Podman test evidence

Run on 2026-10-11 at `5d7eaec21` in a Debian 13 container: Podman 5.4.2, rootless as
uid 1000, pasta networking, cgroups v2, image
`ghcr.io/openhands/agent-server:latest-python` (`17c7a3fcc6dd`). No GPU was available,
so `enable_gpu` is covered by unit tests only.

## Probes

```bash
uv run python .pr/podman_probes.py
```

Source: [`podman_probes.py`](podman_probes.py). Output, with the container's streamed
`[PODMAN]` log lines removed: [`probes-output.txt`](probes-output.txt). Each probe
prints the exact command and its result; the session key is printed as `<key>`.

| Probe | Result |
|-------|--------|
| `GET /api/conversations/count` with `OH_SESSION_API_KEYS_0` set | 401 without the key, 401 with a wrong key, 200 with the key |
| Host processes whose argv contains the key | Only the OpenVSCode server and the `sh` that launches it, both started by the agent server inside the container. No `podman`, `conmon`, or `pasta` process. |
| `env` in the agent's shell | No `SESSION_API_KEY*` variables |
| Host HTTP server on `127.0.0.1`, reached from the agent's shell | Unreachable at `127.0.0.1` and at `host.containers.internal` (curl exit 7) |
| File written to the mounted project | Owned by uid 1000 on the host; the agent runs as uid 10001 in the container |
| `extra_ports=True` | `podman port` shows `8000/tcp -> 127.0.0.1:P` and `8001/tcp -> 127.0.0.1:P+1`; OpenVSCode answers 403 without its connection token |
| After the `with` block | No `agent-server` container and no `podman logs --follow` process |
| Port taken after the availability check | `RuntimeError` with pasta's bind error; no container left |
| Missing volume source | `ValidationError` naming the path; no podman command run |
| Entrypoint exits during startup | `RuntimeError` "Container stopped unexpectedly" with the entrypoint's stderr; container removed |
| Server never healthy, `health_check_timeout=5` | `RuntimeError` "Container failed to become healthy in time"; container removed; log follower stopped |
| Health timeout and the rollback's `podman rm` fails (PATH shim fails the first `rm`) | The health-timeout `RuntimeError` is raised, with the removal failure as a note. The container stays while the exception is alive; releasing it runs `__del__`, which removes the container. |
| `cleanup()` on a running workspace with `podman rm` failing once | First call raises and the container keeps running; second call removes it |

## Example

`examples/02_remote_agent_server/18_convo_with_podman_sandboxed_server.py`, run against
a stub OpenAI-compatible server ([`stub_llm.py`](stub_llm.py)) because no LLM key was
available. The stub's first reply is a terminal call that writes `FACTS.txt`; its next
reply is `finish`. The agent server reaches the stub at `host.containers.internal`.

```bash
python .pr/stub_llm.py 48123 &
LLM_API_KEY=stub LLM_MODEL=openai/stub \
LLM_BASE_URL=http://host.containers.internal:48123/v1 \
uv run python examples/02_remote_agent_server/18_convo_with_podman_sandboxed_server.py
```

Exit 0, `EXAMPLE_COST: 0.0`, `FACTS.txt: uid 1000 (host uid 1000)`, every file in the
project directory owned by uid 1000, and no container left afterwards. Output:
[`example-output.txt`](example-output.txt); requests the stub received:
[`stub-llm-requests.txt`](stub-llm-requests.txt).
