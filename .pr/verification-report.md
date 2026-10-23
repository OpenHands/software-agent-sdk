# Agent Server verification — 2026-10-23

Outcome: **changed, with explicit coverage limits**. This maintenance pass exercised all 34 feature families using `control-agent-server`, reviewed their source and the intervening PR intent, corrected stale recipes, and reported four newly confirmed defects in the fork. Product fixes are separate PRs, documented in the [follow-up fix report](fix-report.md). The accepted maintenance baseline remains unchanged because successful ChatGPT authorization requires human account approval.

## Scope and provenance

| Item | Value |
|---|---|
| Repository receiving writes | `enyst/agent-sdk` only |
| Frozen target | `5966c781ed014efc96c028a99bddd4ff78426741` |
| Accepted baseline | `8ba966d1d3af49f08e04349c055009e1614b48d6` (2026-10-07) |
| Upstream inspected for duplicates/source | `60192ed54db73ac658ac1a07462916126c32d3a0` |
| Server | 1.55.1, launched from isolated target/baseline worktrees |
| Models | DeepSeek Flash and v4 Pro; real ACP adapter requests; explicitly identified local HTTP provider fixtures |
| Host | Linux, Python 3.13, tmux, Chromium, Node, Docker; complete CLI lifecycles supervised by `docker-init -s` |
| Evidence roots | `/tmp/agent-server-verify/20261023-*`; `/workspace/scratch/verify-2026-10-23` |

The fork was 60 commits behind the inspected upstream snapshot and had no fork-only commits. Findings apply to the tested fork: unchanged upstream source is supporting evidence, not a claim of live upstream verification. The clone was not shallow. Product code stayed at the frozen target throughout maintenance.

The Docker host server ran target source. Its child image was `ghcr.io/openhands/agent-server:1.55.1-python-minimal`, digest `sha256:a6ed8b9ab9c7333e1e0ba9c6950636827ee2c8577b0fab1b05cbbd966fdec569`, reporting build `5fa4dea24c52fe3ac53fa4e3e67a1376b72189e2`. This proves that host/released-child combination. The full image could not be unpacked within the workspace disk limit. Browser-negative checks used an isolated minimal container with the frozen target source mounted; they did not modify host Chromium.

## Newly confirmed product bugs

Each defect reproduced twice on a fresh healthy server, with a passing control that distinguishes the failure from setup or transport problems. The new known-bug markers retain the correct expected result and match only the demonstrated symptom.

| Issue | Feature | Expected / observed | Distinguishing control |
|---|---|---|---|
| [#39](https://github.com/enyst/agent-sdk/issues/39) | `F25.install-documented-sources` | Embedded GitHub skill subpath installs with 200 / returns 422 after repository-root renaming | Explicit `repo_path` installs, reads back and uninstalls the same skill |
| [#40](https://github.com/enyst/agent-sdk/issues/40) | `F20.validate-quota-exhausted` | Structured `insufficient_quota` response gives `valid:false` / gives `valid:true` | Ordinary transient 429 remains valid; exact HTTP response recorded; profile store unchanged |
| [#41](https://github.com/enyst/agent-sdk/issues/41) | `F21.ts-payload-types` | Exported profile/diagnostic types accept live `public_skills` / both reject it with TS2353 | Generated OpenAPI types compile the same live payload key sets |
| [#42](https://github.com/enyst/agent-sdk/issues/42) | `F04.child-unborn` | Default child creation in a fresh workspace returns 201 / returns 500 from `git worktree add ... HEAD` | Shared child completes on a real model; committed-repository worktree and non-Git fallback succeed |

Reproduce with the maintenance branch's CLI and recipe map:

```sh
export PATH="$PWD/.agents/skills/verify-agent-server/scripts:$PATH"
control-agent-server map run --file F25 --only F25.install-documented-sources --fresh --repeat 2 --record --keep-going
control-agent-server map run --file F20 --only F20.validate-quota-exhausted --fresh --repeat 2 --record --keep-going
control-agent-server map run --file F21 --only F21.ts-payload-types --fresh --repeat 2 --record --keep-going
control-agent-server map run --file F04 --only F04.child-unborn --fresh --repeat 2 --record --keep-going
```

On a host whose PID 1 does not reap children, wrap each complete command in `docker-init -s --` (or another subreaper). Build the TypeScript client first; real-model recipes require the configured DeepSeek credential. The issue bodies and reviewed evidence packet contain exact observed results and run IDs.

Duplicate checks covered the upstream and fork issue lists, upstream umbrella #5653, fork audit #33, and the existing recipe references. Closed upstream #5895 concerns the earlier parser rejection, whereas #39 reaches installation and fails subsequent directory-name validation. Closed upstream #5100/#4936 concern quota validation/retry behavior; #40 isolates the remaining loss of structured error information on the real HTTP path. The older TypeScript umbrella entry concerned other fields, not newly introduced `public_skills`. No matching child/unborn-repository report was found.

The fork had no recently closed issues. The three recent closed upstream reports inspected for the requested introduction were #6045, #5963 and #5959. New issues use the same introduction with the agent identity accurately changed to Codex: “I'm an AI agent (Codex) helping Engel Nyst (@enyst) with project maintenance.”

## Corrected recipes and harness

Two known-bug markers were retired only after the required **two baseline xfails and two target xpasses**: `F10.fork-tags-consistent` (fork metadata, upstream #4814) and `F14.trajectory-init-path` (deferred-init persistence path, #5696). Their correct assertions remain active. Existing defects fixed only after the frozen fork target retain their markers.

Other changes follow documented source intent: empty credential normalization; tool availability and preload isolation; child REST/tool integration; transient event-stream deltas; active/default Agent Profile repair after deletion (#5983); plugin manifest fallback (#5990); plugin-fetch route ownership; real ACP model discovery and typed clients; Docker lifecycle/startup deadlines; and Git timestamp/provider-response fixtures. Full family replays follow changed executable recipes, with supplemental runs for environment-specific branches.

The CLI now records and requires a healthy `doctor` result before each automatically launched fresh map run. Doctor correctly recognizes dormant deferred-init state, its readiness-before-auth behavior, and WebSocket handshake rejection, and reports failed auth network probes rather than aborting without a report. The HTTP LLM fixture accepts `status:CODE:JSON`, preserving explicit falsey bodies, so structured quota responses can be tested without mocking the SDK. The skill explains process reaping for container hosts.

## Intent and contract review

All 130 commits between baseline and target were resolved through GitHub's commit-to-PR API. The ledger records descriptions, changed paths, review discussion, linked acceptance criteria and REST compatibility comments separately from runtime results. The source wave covered all 34 families, including Python and TypeScript consumers. All 36 changed product paths initially outside a family's source prefixes were reconciled with a family or a concrete non-agent-facing reason.

The route comparison added `POST /api/acp/models`, `POST /api/conversations/{conversation_id}/children`, and `POST /api/plugins/fetch`; it removed or newly deprecated none. ACP discovery already had ownership; the other two now have executable coverage and owners. Successful family checks do not claim that every detail of every PR was independently proven: the runtime overlay records the applicable feature results and capability limits.

The final structural check passes for 34 families and 948 entries. Route coverage reports 206 routes: 196 owned and driven, ten explicitly excluded generated-documentation or Docker catch-all forwarding routes, zero unowned routes, zero owned-but-undriven routes, zero duplicate owners and zero stale exclusions. The forwarding behavior itself has live Docker evidence.

## Coverage limits and resolved prerequisites

The only blocked feature is `F24.login-complete`: successful ChatGPT subscription authorization requires human account approval. Device start/poll/error/logout paths were exercised. The accepted baseline remains unchanged. The user explicitly requested a maintenance PR despite cloud coverage limits, so this PR ships proven corrections without claiming a completely unblocked pass.

The initial environment limits were resolved rather than counted as product failures:

- Docker lifecycle, startup deadline, storage retention, disk-budget cleanup and sync/async SDK consumers ran against a real daemon. The five-minute reclaimer timer was not shortened or called directly.
- Browser-negative checks used an isolated target-source container with no Chromium at standard paths; the positive probe supplied its executable privately.
- All three OpenVSCode lanes ran with the verified public editor binary in an isolated target-source container, including live process readiness, folder encoding, and actual editor access.
- The immutable-filesystem recipe passed in a disposable container. Only its `chattr` helper received `CAP_LINUX_IMMUTABLE`; the actual server ran as UID 1000 with no effective or ambient capabilities. Immutable flags were cleared and the container removed.
- Initial zombie-process assertions failed under this host's non-reaping PID 1. Replaying the complete CLI lifecycle under `docker-init -s` passed with the original assertions. A root-owned test-container evidence directory also interrupted one run-listing check; targeted ownership repair and a fresh replay resolved it.

`F34.event-stream-reconnect-native` is still a known bug on Node 22.22.0. The exact same source and built client xfailed twice at both BASE and TARGET on Node 22, and xpassed twice at both revisions on Node 24.19.0. The watcher now selects the documented affected runtime; the marker was not retired as a product fix. The historical 1.22.0 version-gate fixture pins FastAPI 0.129.0 from that release's lockfile, because today's unconstrained FastAPI no longer imports the old release. The upload-response recipe now asserts the documented `file_size` added by #5856.

No Agent Canvas browser UI, PyInstaller artifact, full Docker image, non-Linux host, or arbitrary third-party provider compatibility is claimed. REST, WebSocket, Python SDK and TypeScript results retain separate evidence. A separate exploratory candidate was stopped by automatic tool screening before confirmation; it was not bypassed, counted as a defect, or used to alter expectations.

## Final results and validation

Snapshot: 2026-10-23T06:44:47+00:00. **948 mapped entries: 864 pass, 82 known-bug xfail, one blocked, one retired.** Every one of the 947 active entries was exercised or explicitly blocked; there are no unexplained failures, unresolved xpasses or unattempted entries. The 82 xfails count feature checks, not distinct issues, and include the four newly reported defects.

The frozen map contained 935 entries. This PR adds 13 and removes none. `F03.auth-before-gate` remains retired inventory, not a runtime pass. These totals select the latest completed target evidence per feature and preserve separate branches. The full evidence tables retain earlier failures, unexpected passes, baseline attempts and subsequent corrections; raw attempt totals must not be mistaken for unique coverage. Parser-only dry runs are excluded from runtime counts.

| Family | Entries | Pass | Known failure | Blocked | Retired |
|---|---:|---:|---:|---:|---:|
| F01 | 11 | 11 | 0 | 0 | 0 |
| F02 | 21 | 20 | 1 | 0 | 0 |
| F03 | 25 | 23 | 1 | 0 | 1 |
| F04 | 36 | 30 | 6 | 0 | 0 |
| F05 | 31 | 28 | 3 | 0 | 0 |
| F06 | 29 | 25 | 4 | 0 | 0 |
| F07 | 24 | 22 | 2 | 0 | 0 |
| F08 | 23 | 20 | 3 | 0 | 0 |
| F09 | 28 | 22 | 6 | 0 | 0 |
| F10 | 25 | 22 | 3 | 0 | 0 |
| F11 | 25 | 24 | 1 | 0 | 0 |
| F12 | 31 | 28 | 3 | 0 | 0 |
| F13 | 29 | 28 | 1 | 0 | 0 |
| F14 | 30 | 27 | 3 | 0 | 0 |
| F15 | 29 | 27 | 2 | 0 | 0 |
| F16 | 26 | 25 | 1 | 0 | 0 |
| F17 | 24 | 24 | 0 | 0 | 0 |
| F18 | 29 | 27 | 2 | 0 | 0 |
| F19 | 28 | 26 | 2 | 0 | 0 |
| F20 | 36 | 32 | 4 | 0 | 0 |
| F21 | 32 | 29 | 3 | 0 | 0 |
| F22 | 24 | 20 | 4 | 0 | 0 |
| F23 | 28 | 24 | 4 | 0 | 0 |
| F24 | 20 | 18 | 1 | 1 | 0 |
| F25 | 33 | 29 | 4 | 0 | 0 |
| F26 | 26 | 24 | 2 | 0 | 0 |
| F27 | 32 | 30 | 2 | 0 | 0 |
| F28 | 29 | 28 | 1 | 0 | 0 |
| F29 | 29 | 28 | 1 | 0 | 0 |
| F30 | 27 | 25 | 2 | 0 | 0 |
| F31 | 23 | 22 | 1 | 0 | 0 |
| F32 | 30 | 27 | 3 | 0 | 0 |
| F33 | 43 | 40 | 3 | 0 | 0 |
| F34 | 32 | 29 | 3 | 0 | 0 |

Validation: `make build`/development dependencies at BASE and TARGET; TypeScript `npm ci` and build at both revisions; **69 CLI tests passed** (39.74 seconds); **25 independent metadata checks passed**; structural/route coverage checks and pre-commit hooks passed. All maintained recipe changes have live evidence or, for the unchanged-script F31 integration, the exact executed shell transcript plus successful map parsing. Product source was not edited in this PR.

Observed SDK-reported model cost was **$0.189769** across 64 captured runs and 211 conversations at the cost snapshot. This is an observed lower bound, not an invoice: snapshots are deduplicated by run/conversation/model maxima, synthetic fixture costs are excluded, and uncaptured validation pings or deleted histories may be missing. ACP adapter labels/rates can differ from actual DeepSeek billing.

Evidence and intent:

- [Reviewed exchanges, controls and provenance](evidence/README.md)
- [Feature and attempt tables](evidence/feature-results.md)
- [Commit/PR intent ledger](intent-ledger.md)
- [Runtime reconciliation](intent-runtime-overlay.md)

The public packet contains selected reviewed fields and metadata. Full raw transcripts remain at the local run paths recorded in provenance; private keys, raw credential exchanges and full server logs were excluded from publication. Publication scans found no configured secret values. All task-owned servers, helpers and containers are stopped after their proofs; evidence is retained.
