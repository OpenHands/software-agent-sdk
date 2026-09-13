# SDK5017: opaque entrypoint before/after in live Canvas

The same UI-created selected profile and uploaded tarball ran on two isolated local stacks. Before the opaque-shell fix, `python3 main.py` saw neither synthetic variable. Afterward it saw `EVIDENCE_ALLOWED` and did not see `EVIDENCE_EXCLUDED`. The completed Automation logs show the synthetic allowed value as `<secret-hidden>`.

Both runs completed. Each real DeepSeek agent independently read the entrypoint's presence-only JSON file using its terminal tool, checked its own environment, and wrote/verified `fixture.json`. No application repositories, GitHub credentials, or external issue/PR operations were involved.

- [Animated actual Canvas sequence](sdk5017-opaque-entrypoint-before-after.gif)
- [Before completed logs](sdk5017-before.png) / [After completed logs](sdk5017-after.png)
- [Exact revisions, bundle hash, run IDs and observations](sdk5017-comparison.json)
- [Reproducible script fixture](sdk5017-fixture.py.txt)

The SDK baseline is integration `ac6d12b0b9d76f1cc38a6eb1ea51cd92e34a0bf2` with only the production `bash_service.py` change from `5209304fb` reversed; after is that integration unchanged. Canvas `4cd1513` and Automation `8abaa0b5` are identical companions. This is a causal comparison of the target change inside the integration, not a claim that the integration equals this standalone PR head. The shared tarball SHA256 is `97981e6d82502099366e59bd1efbaf97d9db80c27b650674d8cabdf87a60d564`.

The local before/after establishes opaque-script delivery and masking. Separately captured local/Docker profile parity is supplementary evidence, using its own disclosed fixture. Agent terminal presence alone would not prove this bug: explicit names already triggered lazy injection before this fix. The before script's missing allowed variable is the reproduced breakage; the run itself can still complete.

GIF frames are genuine browser screenshots with idle intervals shortened; no simulated interface or substituted results. Full local evidence: `factory-state/evidence/sdk-runtime-fixes`.
