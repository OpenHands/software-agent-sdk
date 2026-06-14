# SDK-side fix: skip extra-dependent tests when toolshield is absent

Commit: [`dfa5451a`](https://github.com/OpenHands/software-agent-sdk/pull/2911/commits)
Files touched: `tests/sdk/security/test_toolshield_llm_analyzer.py` (+15 / -1)

## What the previous CI run showed

`sdk-tests` job on the pre-fix PR head: **3766 passed, 4 failed, 13 xfailed**
(the 53 tests in `test_toolshield_llm_analyzer.py` are included in those
totals — 49 passed, 4 failed). All 4 failures share the same root cause:

```
FAILED tests/sdk/security/test_toolshield_llm_analyzer.py::TestSafetyExperiences::test_opt_in_to_default_seed
FAILED tests/sdk/security/test_toolshield_llm_analyzer.py::TestToolShieldHelpers::test_auto_detect_loads_experiences_for_detected_server
FAILED tests/sdk/security/test_toolshield_llm_analyzer.py::TestToolShieldHelpers::test_auto_detect_falls_back_to_default_seed_when_nothing_detected
FAILED tests/sdk/security/test_toolshield_llm_analyzer.py::TestToolShieldHelpers::test_auto_detect_handles_already_inside_event_loop

E   ImportError: toolshield is not installed. Install via
E   `pip install openhands-sdk[toolshield]` to use these helpers, or pass
E   a custom string to ToolShieldLLMSecurityAnalyzer(safety_experiences=...).
```

The four tests genuinely need the real `toolshield` package (they exercise
`default_safety_experiences()` and `auto_detect_safety_experiences()`,
which import and call into `toolshield.experience_store` / `toolshield.mcp_scan`).
The `sdk-tests` job does not install optional extras, so the package
isn't available to those tests.

## The fix

Added a module-level `pytest.mark.skipif` factory:

```python
requires_toolshield = pytest.mark.skipif(
    importlib.util.find_spec("toolshield") is None,
    reason="requires the [toolshield] extra (`pip install openhands-sdk[toolshield]`)",
)
```

…and decorated the four tests with `@requires_toolshield`. Result:

- In `sdk-tests` (no toolshield): the four tests SKIP cleanly instead of failing.
- In a job that installs `[toolshield]` (e.g. the toolshield-specific CI lane): they run normally.
- The 49 other tests in the file already exercise the analyzer through mocks and never needed toolshield — unchanged.

## Why this is the right shape

`toolshield` is declared as an OPTIONAL extra in `pyproject.toml`:

```toml
[project.optional-dependencies]
toolshield = ["toolshield>=0.1.3,<0.2"]
```

So tests that depend on it should follow the standard
`importlib.util.find_spec` + `pytest.mark.skipif` pattern for optional
deps, not assume CI installs every extra. The previous code's docstring
even said "Requires the `[toolshield]` extra (installed in CI)" —
but CI was not, in fact, installing it; the docstring's assumption was wrong.
