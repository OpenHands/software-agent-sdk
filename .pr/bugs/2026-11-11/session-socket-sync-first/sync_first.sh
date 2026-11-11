#!/usr/bin/env bash
# Usage: sync_first.sh TAG   (AGENT_SERVER_VERIFY_RUN must be exported; deepseek preset applied)
# Starts a conversation whose agent runs `sleep 60`, waits until the sleep
# ActionEvent is persisted (run holds the state lock), then opens a session
# socket and sends (a) an invalid assistant message, (b) separately a valid user
# message, recording frame order.
set -u
TAG=$1
C=control-agent-server
CID=$($C conversation start --prompt 'Use the terminal tool to run exactly this command and nothing else: sleep 60 . After it returns, reply DONE and finish.' --print-id)
echo "CID=$CID"
for i in $(seq 1 90); do
  if $C conversation events "$CID" --kinds ActionEvent --contains 'sleep 60' --expect-min 1 >/dev/null 2>&1; then echo "sleep action seen after ${i}s"; break; fi
  sleep 1
done
$C api GET "/api/conversations/$CID" --field execution_status 2>/dev/null | tail -1
# (a) invalid frame during the running step
$C ws listen "/sockets/session/$CID" --send '{"role":"assistant","content":[{"type":"text","text":"x"}]}' \
  --duration 6 --save "F07.sync-first/$TAG-invalid-$CID" --show 10 > "<scratch>/repro/$TAG-invalid.out"
# (b) valid user message during the running step (run:false so it is just saved/queued)
$C ws listen "/sockets/session/$CID" --send '{"role":"user","content":[{"type":"text","text":"QA_VALID_DURING_STEP_'"$TAG"'"}]}' \
  --duration 4 --save "F07.sync-first/$TAG-valid-$CID" --show 10 > "<scratch>/repro/$TAG-valid.out"
echo "CID=$CID" > "<scratch>/repro/$TAG.cid"
# (c) invalid frame again, now listening until the sync frame finally arrives (after the sleep ends)
$C ws listen "/sockets/session/$CID" --send '{"role":"assistant","content":[{"type":"text","text":"x"}]}' \
  --until-kind sync --duration 120 --save "F07.sync-first/$TAG-invalid-until-sync-$CID" --show 10 > "<scratch>/repro/$TAG-invalid-until-sync.out"
$C conversation wait "$CID" --until finished --timeout 240 > /dev/null
$C conversation events "$CID" --kinds MessageEvent --contains "QA_VALID_DURING_STEP_$TAG" --expect-min 1 --save "F07.sync-first/$TAG-valid-persisted" > "<scratch>/repro/$TAG-valid-persisted.out"
echo "valid persisted exit=$?"
