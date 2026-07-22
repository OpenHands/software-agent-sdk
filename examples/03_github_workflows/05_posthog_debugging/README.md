# PostHog self-healing workflow

A recurring, sanitized backend error becomes **one tracking issue**, a
**bounded** OpenHands investigation, and — only after the failure is reproduced
and a regression test goes **red → green** — a **draft** pull request for a human
to review. If the failure cannot be reproduced and proven, the workflow stops at
an issue update. The automation never merges its own PR.

This hardens the earlier PostHog debugging prototype into something safe to run
on a schedule. It is the engineering companion to "Improving workflows over
time".

## How it works — two jobs

```
Job A  triage       contents:read, issues:write        secret: POSTHOG_API_KEY
       Query the sanitized telemetry, group by a line-agnostic dedup_key,
       keep one tracking issue per bug, apply guardrails, emit the eligible list.
                         │  (emits candidates only if the kill switch is on)
                         ▼
Job B  remediate    contents:write, pull-requests:write, issues:write
       1. Agent step (LLM key only, NO git token): a bounded agent proposes a fix
          and writes test.patch / fix.patch / verification.json.
       2. verify.py (agent-free): re-runs the test — must FAIL at the base commit
          and PASS after the fix. It is not the agent, and it cannot be bluffed.
       3. Only if verify passes: open a DRAFT PR. Otherwise update the issue. Never merges.
```

The agent step is deliberately given **no GitHub token**, so the agent physically
cannot push code — only the later steps hold the write token. The verifier runs
as its own step (not inside the agent), so the red→green gate is deterministic.
Job B also sits behind a protected environment (a human approval gate).

## Privacy boundaries

The event source is the **already-sanitized** diagnostic telemetry
(`agent_server.conversation_error` / `request_failed`), which by construction
carries no exception message, no traceback, and no user content — only short
enum-like tokens (`error_class`, `error_category`, `error_origin_module`,
`error_origin_lineno`, `error_fingerprint`) and release identifiers.

What this workflow guarantees on top of that:

- **The query selects an explicit column allowlist** (`sanitize.ALLOWED_EVENT_PROPERTY_NAMES`).
  `distinct_id`, `person_id`, and the raw `properties` blob are never selected;
  `assert_no_pii_keys` fails closed if one ever appears.
- **Every field is re-validated** through the vendored `safe_*` coercions before
  it enters an issue, a log, a job output, or the agent prompt. A byte-match
  drift test (`tests/cross/test_posthog_sanitizer_drift.py`) keeps the vendored
  copy faithful to the emission-side sanitizer.
- **The agent prompt contains only validated tokens** — no message, no payload —
  and frames them as untrusted data, closing the prompt-injection surface.
- **Issue bodies and artifacts carry no identifiers**; developer branch names are
  excluded from "affected releases" (only commit shas / semver versions appear).

### Fingerprint vs. dedup key

The emission-side `error_fingerprint` embeds frame line numbers, so the *same
bug* produces a *different* fingerprint after any unrelated line shift. Tracking,
dedup, and cooldown therefore key on a **line-agnostic `dedup_key`** =
`blake2s(error_class | error_origin_module | error_category)`. The exact
fingerprints are retained only as evidence on the issue.

## Setup

### Secrets

| Secret | Used by | Purpose |
|---|---|---|
| `POSTHOG_API_KEY` | triage | Personal API key with `query:read` |
| `POSTHOG_PROJECT_ID` | triage | PostHog project id |
| `POSTHOG_HOST` | triage | e.g. `us.posthog.com` (optional) |
| `LLM_API_KEY` | remediate (agent) | LLM key for the bounded agent |
| `LLM_BASE_URL` | remediate (agent) | LLM base URL (optional) |
| `SELFHEAL_WRITE_TOKEN` | remediate (PR step) | Optional. Only needed to open PRs in a **different** repo than the workflow's — a fine-grained PAT scoped to Contents + Pull-requests write on the target. Same-repo pilots use the job's `GITHUB_TOKEN`. |

`GITHUB_TOKEN` is provided automatically and scoped per job by `permissions:`.

### Repository variables

| Variable | Effect |
|---|---|
| `POSTHOG_SELFHEAL_ENABLED` | **The kill switch.** Remediation candidates are emitted only when this is `true`. Absent/`false` ⇒ triage still tracks issues but nothing is remediated. |
| `SELFHEAL_LLM_MODEL` | Optional model override (default `claude-sonnet-5`). |

### The human gate

Job B runs in the `self-heal-remediation` **environment**. Add a required
reviewer to it (Settings → Environments). Every remediation run then waits for a
human to approve — a native, audited, per-run stop button. Because PRs are opened
as drafts and the workflow never calls the merge API, combining this with default
-branch protection means the automation cannot land code on its own.

## Operating it

- **Enable:** set `POSTHOG_SELFHEAL_ENABLED=true`.
- **Run:** daily on a schedule, or on demand via *Actions → PostHog Self-Healing →
  Run workflow*. Use the `dry_run` input to refresh tracking issues without
  emitting any remediation candidates.
- **Watch:** each fingerprint gets one issue labeled `self-heal`; progress and
  outcome are posted as comments and reflected in the issue's disposition.

## Rollback

- **Stop everything now:** set `POSTHOG_SELFHEAL_ENABLED=false`. In-flight Job B
  runs also require environment approval, so declining that approval stops a run.
- **Abandon a fingerprint:** the issue keeps being tracked but stops being retried
  once `thresholds.max_remediation_attempts` is reached; close the issue to drop
  it entirely.

## Guardrails (`config.yaml`)

| Rail | Where | Default |
|---|---|---|
| Kill switch | `POSTHOG_SELFHEAL_ENABLED` variable + environment gate | off |
| Minimum occurrences | `thresholds.min_occurrences` | 5 |
| Cooldown | `thresholds.cooldown_hours` | 24h |
| Rate limit / concurrency | `thresholds.max_investigations_per_run` + matrix `max-parallel: 1` | 1 |
| Max attempts | `thresholds.max_remediation_attempts` | 3 |
| Duplicate-run protection | workflow `concurrency:` group per fingerprint | — |
| Agent budget | `agent.max_iterations` | 40 |

## The verification gate

`verify.py` runs as its own step and trusts nothing the agent reports:

1. **Apply `test.patch` at the base commit** and run the regression test — it must
   **fail** (the RED gate). A test that can't be collected counts as "missing" and
   is rejected, not treated as a pass.
2. **Apply `fix.patch` as well** and run again — it must **pass** (the GREEN gate).
3. **The fix may not modify the regression test** (a cheap guard against a "fix"
   that just weakens the test).

Only then is a draft PR opened. The regression test should be a pure unit test.

## Adding a new repository safely

1. Add a target to `targets:` in `config.yaml`: the `error_origin_module` prefix
   that identifies its errors (matched longest-first), the `repo`, and a
   `verification.test_root`. Keep the prefix **specific**.
2. If the target repo differs from the workflow's repo, provide
   `SELFHEAL_WRITE_TOKEN` scoped to Contents + Pull-requests write on it.
3. Protect the target's default branch (required review, bot cannot bypass).
4. A fingerprint is remediated only if it is first-party, matches a prefix, and
   resolves to a base commit; everything else stays at issue-tracking.

## Pilot & metrics

Judge the pilot on **safety**, not fix rate — the sanitized events are thin, so
most fingerprints are legitimately not reproducible, and the *expected* common
outcome is "issue updated, no PR".

Each run appends PII-free rows to `metrics/selfheal-metrics.jsonl` (uploaded as an
artifact) recording the outcome per fingerprint. Read precision / duplicate /
remediation / false-positive off those rows (record a `false_positive` by hand
when a human judges an opened PR wrong).

Run one controlled pilot end to end and confirm:

1. multiple occurrences collapse into **one** issue,
2. no sensitive/untrusted field appears in any artifact or prompt,
3. the agent reproduces the failure,
4. a regression test proves the failure and the fix (red → green),
5. a **draft** PR is opened with evidence,
6. a non-reproducible control case stops at an issue update with **no** PR.

## Files

| File | Role |
|---|---|
| `posthog_debugging.py` | CLI orchestrator (`triage` / `remediate` / `record-outcome`) |
| `telemetry_source.py` | Fixed, column-allowlisted HogQL query + aggregation |
| `sanitize.py` | Vendored validation primitives + PII boundary |
| `fingerprint.py` | `dedup_key`, `SanitizedError`, `FingerprintGroup`, dispositions |
| `repo_map.py` | Module-prefix allowlist + eligibility |
| `issue_tracker.py` | One issue per fingerprint; marker + embedded state |
| `guardrails.py` | Kill switch, cooldown, rate limit |
| `verify.py` | Agent-free red→green gate |
| `metrics.py` | Append-only pilot metrics |
| `config.yaml` | Allowlist + thresholds (the human control surface) |
| `debug_prompt.jinja` | Hardened, injection-resistant agent prompt |
| `workflow.yml` | The two-job GitHub Actions pipeline |

Tests: `tests/github_workflows/test_posthog_selfheal.py` and
`tests/cross/test_posthog_sanitizer_drift.py`.

## Notes

- The verifier assumes a Python/pytest target; the built-in allowlist maps the
  `openhands.*` packages to this repository.
- The agent-driven `test.patch` / `fix.patch` split relies on the agent following
  the prompt's git steps; the verifier re-checks the result, so a malformed split
  fails the gate rather than producing a bad PR.
- Commit and PR authorship use the `openhands-bot` identity.
