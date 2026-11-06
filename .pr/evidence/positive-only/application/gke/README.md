# Fresh personal GKE cross-check

This is an independent fresh-install check, not a second copy of the 64-cell EKS
matrix. Licensed Stable chart 0.90.1, Enterprise 1.79.0, Canvas 1.32.0 and stock
agent-server/SDK 1.58.1 were retained. Automation 1.25.1 was enabled through its
supported Helm values. The approved existing shared GKE Demo org/principal and
working Haiku profile/default were preserved; no user, org or app API key was
created. The application's bundled LiteLLM and isolated upstream LiteLLM are both
1.103.6. Responses and selection/generation charges are synthetic.

Five focused application checks completed:

- Stock-runtime chats for usage-only positive cost, zero-header conflict and
  aggregate-router usage all retained $0, reproducing the original accounting
  problem without the EKS lab's upgrade history.
- A matching-positive-header automation with normal service cleanup completed
  with callback cost $0.04 but linked app cost $0 after the sandbox became MISSING.
- The matching retained-runtime control waited for app metrics, then cleanup
  retained $0.04. Thus the default-cleanup persistence issue also exists on this
  fresh install with stock SDK 1.58.1, independently of the SDK patch/test image.

Additionally, both immutable baseline and positive-only SDK wheels ran 16 real
SDK Conversations each through this GKE bundled gateway and upstream fixture.
Actual streaming and non-streaming were verified by the correlated backend
ledger. Usage-only changed $0 → $0.04 and aggregate router usage $0 → $0.05;
zero-header and generation-only router limitations were preserved. These SDK
clients ran locally against GKE; a candidate GKE agent-server image was not
deployed. Candidate application-runtime qualification is the separate EKS matrix.

`summary.json` retains exact IDs, callback/app values, settled-control comparison
and environment identities. The SDK results files retain per-run backend IDs and
hashes. Auth/session/credential values and prompts are excluded. Main app tests
streamed because Enterprise forces it on; actual non-streaming is supplementary
SDK coverage. No customer upstream selector or paid model-selection call ran.
