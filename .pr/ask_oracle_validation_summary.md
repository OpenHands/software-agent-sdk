# `ask_oracle` validation evidence

These PR-only artifacts were regenerated against source commit
`6753afc5898c367955b2f984687784f59d70a004` on 2026-08-27. The commit that adds
this directory contains evidence only; it does not change product code.

## What the evidence proves

The live validation drives a normal `LocalConversation` agent loop with a
primary model and a separate saved profile named `oracle`. It does not call the
executor directly.

The result in `.pr/ask_oracle_live_validation.json` records eight passing
checks:

- The primary agent emitted exactly one `AskOracleAction`.
- The agent loop received exactly one successful `AskOracleObservation`.
- The Oracle's independently captured completion log matches the observation
  byte-for-byte.
- The `ORACLE_LIVE_OK` sentinel traveled through the Oracle response,
  observation, and final agent answer.
- The primary model remained unchanged after consulting the Oracle.
- The conversation finished normally.

The live run used `openai/gpt-5.1` as the primary model and
`openai/gpt-5-mini` as the `oracle` profile through the eval proxy. Its recorded
cost was `$0.0186345`. The profile store and Oracle completion logs lived under
a `TemporaryDirectory`, exercising the new `profile_store_dir` path without
touching the user's default profile store.

## Additional verification

- Focused tool and custom-profile-directory tests: `8 passed`.
- Checked-in example through the repository example harness: `1 passed`.
- Pre-commit on the live-validation script: passed.
- GitHub CI for the validated source commit: 36 successful, 0 failed, 0
  pending; the PR has the `integration-test` label.

Exact commands and counts are recorded in
`.pr/ask_oracle_test_results.json`. The validation script is included so
reviewers can reproduce the live result without exposing credentials.
