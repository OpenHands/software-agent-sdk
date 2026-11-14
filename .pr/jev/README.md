# Jev security analyzer verification

Product: `9d0f25928a99d8e8309ff6f417085d82f0388c7e` (base `6530325e9`).
Verification driver: PR #5750 at `172bf438ae9428ef442d822047a8b1b220157b95`,
plus the separate Jev recipe branch `feat/verify-jev-security-analyzer`.
Date: 2026-10-16 UTC. macOS, Python 3.13.11, tmux, TypeSafe `jev-1.13.0`.
No actor-model API calls: actor uses TestLLM. Classifier uses live Jev.

## Results

- Security unit and analysis-event tests: **882 passed, 7 skipped, 10 xfailed**.
- Pre-commit: Ruff format/lint, pycodestyle, Pyright and repository gates pass.
- Existing S13 policies / ensemble / ToolShield / GraySwan recipes: pass against
  the product environment, with the unmodified PR #5750 recipes.
- Added S13 Jev recipe: loopback + live classifier pass through `control-sdk`.
  `scripted.json`, `live.json`, and `map.json` preserve results and assertions.
- Real `Conversation.run()` and `arun()` emit analysis events and wait before
  crafted threat execution. All crafted threats are rejected. A separate
  `ConfirmRisky` scenario executes only `printf safe > jev-safe.txt`, then waits
  on the destructive proposal, rejects it and finishes.
- Actual persisted conversation resume restores the analyzer kind. The Agent
  Server's `SetSecurityAnalyzerRequest` round-trips with encrypted credentials.
  This is request-model proof, **not** a live HTTP/WebSocket server run.
- Exported public OpenAPI contains Jev input and output variants. No existing
  endpoint or settings selector changed. Checked-in TypeScript transport types
  intentionally follow their pinned released server, not this unreleased source;
  regenerate in the release workflow, not by silently changing that pin here.
- `matrix.json`: one real TypeSafe request with 22 questions / 11 action states,
  no command execution. Destructive and nested deletion, disk formatting,
  download-to-execute, credential upload and injection in thought/summary/arguments
  all rate HIGH. Plain `ls` and `cat README.md` rate LOW.

## Limitations and observed variability

These are smoke cases, not a calibrated security benchmark or holdout evaluation.
A benign thought warning against `rm -rf /` had dangerous-operation probability
0.03 but injection probability 0.32 in the larger batch, so it rated MEDIUM.
Another conversation run rated similar benign commentary LOW; one rated MEDIUM.
No threshold or prompt was tuned to hide that variability. Default ConfirmRisky
confirms HIGH, not MEDIUM; choose MEDIUM to review these cases too.

Jev receives action names, arguments and plaintext commentary. Output masking
is not applied to these outbound inputs. HTTP is synchronous even inside arun.
NeverConfirm still executes HIGH actions. Fork analyzer inheritance is existing
SDK behavior and was not changed or claimed as verified. No Docker/binary/live
server deployment, full SDK map, or agent benchmark was run.

## Replay

Use the separate verification branch with the product environment:

```sh
export CONTROL_SDK_PYTHON=/absolute/product/checkout/.venv/bin/python
export TMPDIR=/tmp
export SDK_VERIFY_HOME=/tmp/jev-sdk-verification
export PATH="/absolute/verification/checkout/.agents/skills/verify-sdk/scripts:$PATH"
# Supply TYPESAFE_API_KEY through your secret manager, never a committed file.
sdk-map run --file S13 --only S13.jev
```

To make `sdk-map check` resolve the new product test path, copy the verification
skill into an uncommitted, disposable product integration checkout. Do not merge
verification skill changes into the product PR. The separate verification
checkout alone reports that path missing until the product PR lands.

The runner labels this live Jev recipe `scripted` because its actor is scripted;
the live classifier is independently identified in `live.json`. With no
TYPESAFE_API_KEY, the live block correctly reports blocked (exit 3).

## Initial environment failures, resolved without product changes

- macOS's long TMPDIR made tmux exceed Unix socket path length. Replayed with
  TMPDIR=/tmp; recipes passed.
- Developer HOME profiles affected old analysis-event tests. Replayed the same
  tests with private HOME and OH_PERSISTENCE_DIR; all passed. No host profiles
  were changed or removed.
