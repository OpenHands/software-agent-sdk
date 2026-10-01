#!/usr/bin/env bash
# Capture before/after MiniMax evidence for PR #5412.
#
# Requires a litellm proxy whose OH_CLASSIFIER_MODEL group is backed by MiniMax:
#   export OH_PROXY_URL=http://127.0.0.1:4000
#   export OH_PROXY_KEY=sk-...
#   export OH_CLASSIFIER_MODEL=minimax-m3
#   export OH_TARGET_MODEL=gpt-5.5
#
# Produces before.log (base 3c8be74, expect 400 "chat content is empty") and
# after.log (HEAD, expect success). Run from the repo root on the PR branch.
set -euo pipefail

cd "$(dirname "$0")/../.."
SRC=openhands-sdk/openhands/sdk/tool/builtins/classify_and_switch_llm.py
BASE=3c8be74

echo "=== AFTER (HEAD) ==="
uv run python .pr/evidence/repro.py 2>&1 | tee .pr/evidence/after.log || true
echo

echo "=== BEFORE (base $BASE) ==="
git checkout "$BASE" -- "$SRC"
trap 'git checkout HEAD -- "$SRC"' EXIT
uv run python .pr/evidence/repro.py 2>&1 | tee .pr/evidence/before.log || true
