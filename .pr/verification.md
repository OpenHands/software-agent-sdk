# SDK #4976 verification

Environment: macOS arm64, Python 3.13.3, LiteLLM 1.93.0, SDK 1.49.4.
Implementation base: `3f47426e6`. No dependency or lockfile changes.

## Behavior checked

- Before fixing completion selection, the new stream matrix reproduced six
  failures: yielded completions overwritten by stale `None`, and initial wrapper
  completions cleared during iteration. Afterward all 15 cases passed.
- Sync/async streaming, sync streams returned to async callers, non-null wrapper
  precedence, plain generators, and missing-completion errors are covered.
- Collected output items reconstruct empty final Responses output, including
  required streams without callbacks. Delta chunk IDs remain stable.
- Typed provider objects, generic LiteLLM objects, dictionaries, nested attribute
  objects, absent fields, replay IDs, encrypted reasoning, signed/redacted thinking,
  and non-native tool-call reasoning metadata retain their behavior.
- Tokenizer result-shape tests cover ID lists, nested lists, mappings, shapes,
  encodings, rendered strings, and unsupported results with fallback.
- API-key configs are unchanged; subscription configs restore credentials and
  reuse already-restored runtime instances.
- The SDK dynamic-access baseline drops from 98 entries to 84, removals only.
  The unrelated Pydantic field-exclusion lookup remains outside this issue.

## Automated checks

```sh
make build
uv run pytest tests/sdk/llm/ tests/cross/test_check_forbidden_dynamic_attributes.py -q
uv run pytest tests/sdk/ -q -o addopts='--tb=short -m "not stress and not acp_live"'
uv run pre-commit run --all-files
uv run python scripts/check_forbidden_dynamic_attributes.py --baseline-ref upstream/main
```

- Final complete LLM/checker run: **1,073 passed**, 18 model-cost warnings.
- Additional stream-reconstruction/serialization/thinking check: **66 passed**.
- Full SDK rerun: **6,432 passed, 9 skipped, 19 deselected, 12 xfailed**, 64
  warnings, in 221.31 seconds. The two newly added no-callback reconstruction
  cases were collected after this run began and passed in the focused run above.
- All repository pre-commit checks passed, including whole-repository Pyright.
- The baseline check against `upstream/main` passed; the diff contains removals only.

The first broad SDK run passed 6,226 tests but encountered 208 filesystem setup
errors (`ENOSPC`, no space left on device) while PyInstaller was also building.
This is not counted as a successful suite run. Verification was rerun sequentially.

## Real local transport check

```sh
OPENHANDS_SUPPRESS_BANNER=1 uv run python .pr/local_transport_smoke.py
```

Uses the actual SDK, LiteLLM, and HTTP/SSE parsers against a loopback HTTP server;
no provider function is mocked and no external model credentials are used.
The server is shut down and its thread joined on exit.

```text
PASS: responses, async=False, streaming=False
PASS: responses, async=True, streaming=False
PASS: responses, async=False, streaming=True
PASS: responses, async=True, streaming=True
PASS: chat, async=False, streaming=True
PASS: chat, async=True, streaming=True
```

Assertions check final text, callback text, stable Responses delta IDs, and
output reconstruction when `response.completed` contains an empty output list.
This checks local transport integration, not a live provider service.

## Optional Transformers check

```sh
OPENHANDS_SUPPRESS_BANNER=1 uv run python .pr/tokenizer_smoke.py
OPENHANDS_SUPPRESS_BANNER=1 uv run --isolated --frozen \
  --with transformers==5.17.0 python .pr/tokenizer_smoke.py
```

Both passed. The regular workspace has no Transformers installed. The isolated
environment uses a local WordLevel tokenizer saved to a temporary directory:
real `AutoTokenizer` loading succeeds, and `LLM.get_token_count()` returns exactly
2 tokens. No model files are downloaded and PyTorch is not required.

## Packaged Agent Server

`make build-server` succeeded on the standalone retry, producing the macOS arm64
`dist/openhands-agent-server` executable. PyInstaller analysis includes all
three new private SDK modules. The first run failed during packaging while the
machine was out of disk space and the full SDK suite ran concurrently.
No changes to the server spec or dependency installation are part of this PR.
The binary's `--help` command succeeded. A separate loopback startup check
returned HTTP 200 from `/health` and stopped its server process:

```sh
OPENHANDS_SUPPRESS_BANNER=1 uv run python .pr/server_binary_smoke.py
```

## Companion documentation

Updated `sdk/arch/llm.mdx` and `sdk/guides/llm-streaming.mdx` in OpenHands/docs.
The streaming guide now describes Responses streaming and completion precedence.

```sh
uv run --with pytest --with requests --with pyyaml pytest -q tests/
```

Result: **35 passed, 2 errors**. Both errors are existing pricing-test fixtures
fetching `OpenHands/OpenHands/main/openhands/utils/llm.py`, which returns HTTP 404.
No pricing pages or tests were changed. The initial documented test command also
needed `pyyaml` added to its ephemeral environment. No docs dependencies were edited.

## Attribution

Completion-selection semantics follow alanhuangyoo's closed, unmerged SDK PR
[#4772](https://github.com/OpenHands/software-agent-sdk/pull/4772), which the issue
designates authoritative. The new regression matrix builds on that report.
