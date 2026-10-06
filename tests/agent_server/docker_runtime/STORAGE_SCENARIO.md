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

It runs the real app in-process against real containers:

1. Start two conversations, A and B. Inside each container, write what installs
   would: `~/.cache/uv`, `~/.npm`, a git-ignored `node_modules/` and a tracked
   `main.py` in `/workspace`.
2. Stop A (`DELETE /api/conversations/{A}/runtime`).
   - A's `~/.cache` and `~/.npm` are gone.
   - A's `node_modules/` and `main.py` are kept.
   - B, still running, keeps everything.
3. Put the server over its disk budget and run one maintenance pass.
   - A's `node_modules/` is gone; `main.py` and `.gitignore` are kept.
   - B, still running, keeps everything.
   - The trash is empty afterwards.

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
