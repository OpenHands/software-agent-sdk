# Enterprise two-proxy qualification

November 5, 2026. Approved target: isolated EKS native-auth lab, Enterprise 1.79.0,
automation 1.25.1, Canvas 1.33.0. Two real LiteLLM 1.103.6 proxies; packaged SDK
baseline/candidate 1.60.1, SDK LiteLLM client 1.105.1. The application's bundled
proxy is the first hop; an isolated proxy is the second. The backend scripts
OpenAI responses and synthetic selection/generation charges. No customer upstream
endpoint or real paid router/selector was used.

`summary.json` contains 64 main cells (8 contracts × chat/manual prompt automation
× saved stream setting × baseline/candidate) plus two default-cleanup controls.
All main conversations finished and all main automation runs completed. Enterprise
forces profile inference to stream: stream=False cells are compatibility
observations, not independent non-streaming coverage. Actual non-streaming is
covered by the separate packaged SDK HTTP/two-proxy results.

Chats include an auxiliary title request: one agent and one title call. Automations
have one agent call. Compare all billed attempts, not one assumed call per chat.
Some app snapshots lagged completion; settled totals are recorded separately from
initial reads. Candidate main automations wait for callback/app cost convergence
before cleanup and verify the persisted total afterward.

| Contract | Baseline auto callback | Candidate auto callback / settled app | Candidate chat settled app | Ledger auto/chat |
| --- | --- | --- | --- | --- |
| Usage only | $0 | $0.04 | $0.08 | $0.04 / $0.08 |
| Zero header + positive usage | $0 | $0 | $0 | $0.04 / $0.08 |
| Matching header | $0.04 | $0.04 | $0.08 | $0.04 / $0.08 |
| Conflicting header | $0.03 | $0.03 | $0.06 | $0.04 / $0.08 |
| Router aggregate usage | $0 | $0.05 | $0.10 | $0.05 / $0.10 |
| Router generation-only reporting | $0 | $0.04 | $0.08 | $0.05 / $0.10 |
| Router generation-only header | $0.04 | $0.04 | $0.08 | $0.05 / $0.10 |
| Confirmed free | $0 | $0 | $0 | $0 / $0 |

Matching the selection contract is not the same as matching the billing ledger.
The zero/conflicting/header-excludes-selection rows deliberately expose unresolved
precedence or reporting contracts. The SDK selects one charge; it never sums two
overlapping representations or invents an absent selection fee.

## Additional default-cleanup finding

Both extra candidate prompt automations used the normal `keep_alive=false`
service cleanup. One had usage-only positive cost; the other a matching positive
header. Both callbacks reported $0.04, while linked app records retained $0 and
had no live execution status after their sandbox became MISSING. This reproduces
an app/automation cost-persistence gap independent of the usage fallback. Four
baseline app records also froze at zero when our first harness cleaned up before
their metrics converged. Retained-runtime candidate controls converged and kept
the correct amount after controlled cleanup. Do not call default cleanup solved.

The follow-up contract is to durably persist final metrics/status before sandbox
deletion, including auxiliary calls, and verify app/API/UI totals after deletion
or restart. Source ownership and release-specific remediation need separate review;
this SDK PR does not modify Enterprise or automation code.

## Artifacts and reproduction

- `correlated-runs.json`: original per-cell IDs, backend request IDs and synthetic stage ledgers; credentials and prompts are excluded.
- `summary.json`: exact IDs, settled versus initial totals, callback totals,
  observed stream modes, billed-call counts and both default-cleanup controls.
- `baseline-chat-usage.png`, `candidate-chat-usage.png`: real UI shows $0.0000
  versus $0.0800 for the same two $0.04 charges.
- `cases.json`, `fixture.py`, `chart/`: the real-proxy fixture contract and pinned
  images. Create an isolated namespace and your own Secret named by `existingSecret`
  with PROXY_A_KEY, PROXY_B_KEY and SYNTHETIC_PROVIDER_KEY; never reuse production
  credentials. Package fixture.py/cases.json at the Helm chart root before install.
- `runtime.Dockerfile`, `runtime-requirements.txt`: test-only normal-wheel runtime
  build; both variants share the same released OS/tools base and hash-locked deps.
  Build matching agent-server/tools wheels and baseline/candidate SDK wheels first.
  These source-mode test images are not published OpenHands releases.
- `sdk-baseline-results.json`, `sdk-candidate-results.json`: 32 supplementary
  real SDK Conversations through two proxies, with actual streaming on/off.

App runs use supported org profiles, first-class app conversations and manually
dispatched prompt presets. Definitions are disabled after testing; no cron or
external webhook was used. No account, org, API key, outbound message or PR was
created for these tests. Local origin-bound credentials remain private.

Fresh-install cross-check: [personal GKE results](gke/README.md), including stock-runtime reproduction and supplementary packaged SDK requests through the GKE bundled gateway.
