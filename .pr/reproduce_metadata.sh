#!/usr/bin/env bash
set -euo pipefail

# Run from the repository root. No model requests or tmux are needed.
# bash .pr/reproduce_metadata.sh /path/to/base fail
# bash .pr/reproduce_metadata.sh "$PWD" pass
checkout=${1:?checkout path required}
expected=${2:?expected result must be pass or fail}
cli="$PWD/.agents/skills/verify-agent-server/scripts/control-agent-server"
a=
b=
cleanup() {
    if [[ -n "$b" ]]; then "$cli" stop --run "$b" >/dev/null; fi
    if [[ -n "$a" ]]; then "$cli" stop --run "$a" >/dev/null; fi
}
trap cleanup EXIT

for mode in graceful hard; do
    for attempt in 1 2; do
        a=$("$cli" launch --new --checkout "$checkout" --name "metadata-$mode-$attempt" --print-run)
        "$cli" doctor --run "$a"
        cid=$("$cli" conversation start --run "$a" --placeholder-agent --tools none \
            --no-autotitle --title 'before B' --no-run --print-id)
        b=$("$cli" launch --new --checkout "$checkout" --name metadata-observer \
            --share-conversations-from "$a" --print-run)
        "$cli" doctor --run "$b"
        "$cli" api PATCH "/api/conversations/$cid" --run "$a" \
            --json '{"title":"after B","tags":{"stage":"latest"}}' \
            --expect 200 --check success eq true --save F31.takeover-keeps-metadata/update
        updated=$("$cli" api GET "/api/conversations/$cid" --run "$a" --field updated_at)
        "$cli" state cat meta.json --conversation "$cid" --run "$a" --check title eq 'after B'
        if [[ "$mode" == hard ]]; then
            "$cli" restart --run "$a" --hard
            generation=2
        else
            "$cli" stop --run "$a"
            generation=1
        fi
        "$cli" api POST "/api/conversations/$cid/pause" --run "$b" \
            --expect 200 --check success eq true
        pidb=$(jq -r .pid "$b/run.json")
        "$cli" state cat owner_lease.json --conversation "$cid" --run "$a" \
            --check owner_pid eq "$pidb" --check generation eq "$generation"
        result=pass
        "$cli" state cat meta.json --conversation "$cid" --run "$a" \
            --check title eq 'after B' --check tags.stage eq latest \
            --check updated_at eq "$updated" || result=fail
        "$cli" api GET "/api/conversations/$cid" --run "$b" \
            --check title eq 'after B' --check tags.stage eq latest \
            --save F31.takeover-keeps-metadata/get-after-takeover || result=fail
        current=$("$cli" api GET "/api/conversations/$cid" --run "$b" --field updated_at)
        [[ "$current" == "$updated" || "$current" > "$updated" ]] || result=fail
        "$cli" evidence add --run "$b" --feature F31.takeover-keeps-metadata \
            --result "$result" --entry "$mode takeover: meta.json and GET" \
            --expected 'latest title and updated_at survive' --actual "$result" \
            --artifact evidence/F31.takeover-keeps-metadata/get-after-takeover.json
        cleanup
        a=
        b=
        [[ "$result" == "$expected" ]]
    done
done
