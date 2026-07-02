# `.pr/` — live evidence for PR #2911

Following the convention @enyst suggested in
[review comment](https://github.com/OpenHands/software-agent-sdk/pull/2911#issuecomment-4662680235):
artefacts proving a fix works belong under `.pr/`, not just pasted in PR comments.

## What this bundle answers

Both blockers raised on this PR:

1. **@VascoSch92 — "fix the package at the source first"**
   ([CHATS-lab/ToolShield#4](https://github.com/CHATS-lab/ToolShield/issues/4)).
   Fixed and published as `toolshield==0.1.3`. Files `01`–`04` below are
   the evidence that 0.1.3 is correct and reproducible from source.

2. **@enyst — "add logs or other artefacts that show it works"**.
   That's this directory.

## Files

| file                          | what it shows                                                                                                                                                                          |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pypi-0.1.3.json`             | Raw `pypi.org/pypi/toolshield/0.1.3/json` response — canonical record of what was uploaded.                                                                                            |
| `01-pypi-metadata.txt`        | Same data, human-readable: version, upload time, filenames, sizes, SHA256s.                                                                                                            |
| `02-reproducible-build.txt`   | Rebuilt the wheel + sdist locally from `git archive HEAD` on CHATS-lab/ToolShield. SHA256s are **byte-identical** to PyPI — `pip install toolshield==0.1.3` is what's in source-of-truth. |
| `03-wheel-contents.txt`       | Full `unzip -l` of the wheel. Confirms `mcp_scan.py`, `experience_store.py`, and the six bundled `claude-sonnet-4.5` experience JSONs all ship.                                        |
| `04-pypi-install-smoke.txt`   | Fresh venv → `pip install toolshield==0.1.3` from PyPI → reporter's smoke test, every assertion passes. This is the failure mode from #4 actually exercised.                           |
| `05-sdk-test-fix.md`          | Note explaining the small `tests/sdk/security/test_toolshield_llm_analyzer.py` change in this PR — adds `requires_toolshield` skip marker for the 4 tests that need the optional extra. |

## Commits in this PR addressing the review

| commit       | what                                                                          |
| ------------ | ----------------------------------------------------------------------------- |
| `3c87453`    | Pin bump: `toolshield>=0.1.1,<0.2` → `>=0.1.3,<0.2`                           |
| `dfa5451a`   | Skip the 4 toolshield-dependent tests when the extra isn't installed         |

(Earlier commits in the PR — `ebc6fcd4` through `b4f92775` — addressed
the two prior rounds of review feedback from @Fieldnote-Echo.)

## How to re-verify locally

```bash
# 1. Confirm toolshield package is fixed
python -m venv /tmp/verify
/tmp/verify/bin/pip install toolshield==0.1.3
/tmp/verify/bin/python -c '
from toolshield import ExperienceStore
from toolshield.mcp_scan import scan_port              # missing in 0.1.2
from toolshield.cli import main                        # toolshield auto entry point
ExperienceStore().load_bundled("filesystem-mcp")
print("OK")
'

# 2. Confirm SDK tests pass (with the [toolshield] extra so the 4 marked
#    tests don't skip)
pip install -e "openhands-sdk[toolshield]"
pytest tests/sdk/security/test_toolshield_llm_analyzer.py -v

# 3. Confirm they SKIP cleanly without the extra
pip uninstall -y toolshield
pytest tests/sdk/security/test_toolshield_llm_analyzer.py -v -k "auto_detect or opt_in_to_default_seed"
# Expected: 4 SKIPPED with reason "requires the [toolshield] extra"
```
