# Storage reclaim scenario (Docker runtime)

What a Docker-mode agent-server must do with the storage its conversations
leave behind. Run it after changing `openhands/agent_server/storage/` or
`docker_runtime/`, or when bumping the agent-server image.

## Automated

Needs a running Docker daemon and the agent-server image (pulled, not built):

```
docker pull ghcr.io/openhands/agent-server:latest-python
uv run pytest -m docker_live tests/agent_server/docker_runtime/test_storage_scenario.py
```

`OH_STORAGE_SCENARIO_IMAGE` selects another image, e.g. one built from the
branch. The test is deselected by default (`docker_live` marker) and skips
when Docker or the image is missing.

Each test configures the server through `OH_*` environment variables, as the
CLI does, and drives real containers. Every one checks what must survive next
to what must go:

1. **Only rebuildable files go.** Two conversations write caches, shell
   history, a config file, a committed repo with untracked notes, an ignored
   `.env`, `node_modules/`, `.venv/`, `build/`, a clone inside an ignored
   `vendor/`, and a nested checkout with its own `node_modules/`. Stopping one
   drops its `~/.cache` and `~/.npm` only. A pass over the disk budget then
   sheds its four dependency dirs and nothing else. The running conversation is
   unchanged, file for file.
2. **Planted links are removed, never followed.** The sandbox points `~/.npm`
   and an ignored `build` at host paths outside the runtime, and `~/.cache` at
   a relative path that lands in its own workspace on the host. All targets
   survive.
3. **A caller-supplied workspace is never shed**, even over budget; the
   sandbox's own caches still go.
4. **A reclaimed conversation resumes and works**: reading a workspace file
   through the API restarts it, its commit is there, and it can write caches
   and dependencies again.
5. **An archived conversation is read-only until deleted** (retention 1 day,
   one conversation aged 2 days): its runtime is gone, its history still reads,
   the runtime routes answer 410, the recent one is kept, and delete still
   removes everything.

Mutation-checked: breaking the cache drop, the budget pass, the clone
protection, the running-runtime guard, symlink unlinking, the caller-workspace
rule or the 410 each fails the matching test.

## By hand

Against a real server, for when the test itself is in doubt. Run from an empty
directory; the server keeps everything under `./workspace/`.

```
OH_SECRET_KEY=$(openssl rand -hex 32) OH_CONVERSATION_RUNTIME=docker \
  OH_CONVERSATION_STORAGE_DISK_BUDGET=0.01 \
  uv run --project <sdk checkout> python -m openhands.agent_server --port 8000
```

`0.01` puts any real disk over budget. In a second shell, start two
conversations and write what installs would. The runtime directories are bind
mounts: `persistence/` is the sandbox's `$HOME`, `workspace/` its `/workspace`.

```
start() {
  curl -s localhost:8000/api/conversations -H 'content-type: application/json' \
    -d '{"agent": {"kind": "Agent", "llm": {"model": "test"}, "tools": []}}' | jq -r .id
}
A=$(start); B=$(start)
for id in $A $B; do
  dir=workspace/.openhands/runtime-data/${id//-/}
  mkdir -p $dir/persistence/.cache/uv $dir/workspace/node_modules/left-pad
  echo x > $dir/persistence/.cache/uv/wheel.whl
  echo 1 > $dir/workspace/node_modules/left-pad/index.js
  echo 'print(1)' > $dir/workspace/main.py
  (cd $dir/workspace && git init -q && echo node_modules/ > .gitignore)
done
curl -s -X DELETE localhost:8000/api/conversations/$A/runtime
```

Expected:

- Right after the stop, A's `persistence/.cache` is gone and its
  `workspace/node_modules` is still there.
- Within five minutes (the maintenance interval) the server logs
  `Conversation storage: freed …`, and A's `workspace/node_modules` is gone;
  `main.py` and `.gitignore` stay.
- B keeps all of it while it runs.
