# Meta-profile routing on Agent Profiles: end-to-end evidence (2026-09-30)

`.pr/launch_docker_e2e.py` ran against this branch and its base (#5406), with the
canvas mock LLM and a fresh state directory. The host runs natively in Docker
runtime mode. Each container image is built from the same commit, and the
container's HOME has no stores. The profile enables the routing tool and names a
meta-profile whose classifier and class use two saved LLM profiles (`mock`,
`mock-fast`).

| Check | #5406 | branch |
|---|---|---|
| a profile with `meta_profile_ref` can be saved | FAIL (422) | PASS |
| routing works without stores in the container: the routing tool carries the meta-profile and both LLMs | FAIL | PASS |
| a dangling `meta_profile_ref` fails on the host with one structured 422 (`dangling_meta_profile_ref`), no container started | FAIL (profile cannot be saved) | PASS |
| the other 12 checks of #5406 | PASS | PASS |

The branch passes 14/14 with and without chromium in the container.

## Unit suites

| Suite | #5406 | branch |
|---|---|---|
| `tests/sdk` | 6656 passed, 1 failed (`test_truncate`) | 6660 passed, same 1 failed |
| `tests/agent_server` | 2327 passed, 7 failed (`canvas_extensions`, macOS) | 2327 passed, same 7 failed |
| `tests/cross` + `tests/workspace` | 698 passed | 697 passed. The gateway live test failed once under xdist (libtmux could not connect to its socket); the file passes serially (21 passed). |
