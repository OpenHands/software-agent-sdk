# Package Guidelines

See the [project root AGENTS.md](../../../AGENTS.md) for repository-wide policies and workflows.

## Package Structure & Module Organization

- This directory (`openhands-sdk/openhands/sdk/`) contains the core Python SDK under the `openhands.sdk.*` namespace.
- Keep new modules within the closest existing subpackage (e.g., `llm/`, `tool/`, `event/`, `agent/`) and follow local naming patterns.
- Add/adjust unit tests under `tests/sdk/` mirroring the SDK path (for example, changes to `openhands-sdk/openhands/sdk/tool/tool.py` should be covered in `tests/sdk/tool/test_tool.py`).

## Build, Test, and Development Commands

- `make build`: sets up the dev environment (runs `uv sync --dev` and installs pre-commit hooks).
- `make lint` / `make format`: run Ruff linting and formatting.
- `uv run pre-commit run --files <path>`: run the pre-commit checks for files you changed.
- `uv run pytest tests/sdk -k <pattern>`: run targeted SDK tests; prefer running the smallest relevant test set first.

## Coding Style & Naming Conventions

- Python target is 3.12; keep code Ruff-compliant (line length 88).
- Prefer explicit, accurate type annotations; use Pyright for type checking (do not add mypy).
- Avoid `# type: ignore` unless there is no reasonable typing fix.
- Keep imports at the top of files; avoid `sys.path` hacks and in-line imports unless required for circular dependencies.
- When changing Pydantic models or serialized event shapes, preserve backward compatibility so older persisted data can still load.

## Testing Guidelines

- Prefer real code paths over mocks; introduce fixtures in `tests/conftest.py` when setup is repeated.
- Keep tests minimal and focused on the changed behavior; avoid adding broad integration tests unless required.

## Bedrock + LiteLLM note

- LiteLLM interprets the `api_key` parameter for Bedrock models as an **AWS bearer token**.
  When using IAM/SigV4 auth (AWS credentials / profiles), do **not** forward `LLM.api_key`
  to LiteLLM for Bedrock models, or Bedrock may return:
  `Invalid API Key format: Must start with pre-defined prefix`.
- If you need Bedrock bearer-token auth, set `AWS_BEARER_TOKEN_BEDROCK` in the environment
  (instead of using `LLM_API_KEY`).
- Prefer `openhands.sdk.llm.utils.litellm_provider.LLMProvider` for LiteLLM-facing runtime
  logic instead of manually splitting `LLM.model`. Accept the full model string at the SDK
  boundary, then normalize immediately into LiteLLM's parsed `provider` + `model` view.
- Do not duplicate multiple provider objects for transport versus capabilities. Initialize
  the LiteLLM-facing transport provider once during `LLM` setup, and keep
  capability/feature lookups on the canonical model string (`LLM.model_canonical_name` or
  `LLM.model`).
- Avoid per-call provider cache-refresh logic in `LLM`. If the provider/model changes,
  construct a new `LLM` instance rather than trying to mutate transport provider state in
  place.
- Keep `unverified_models` conservative for UI bucketing: LiteLLM inference is useful for
  transport behavior, but it can classify ambiguous raw IDs (for example regional Bedrock IDs)
  more aggressively than the UI should.

## Event Type Deprecation Policy

When modifying event types (e.g., `TextContent`, `Message`, or any Pydantic model used in event serialization), follow these guidelines to ensure backward compatibility:

### Critical Requirement: Old Events Must Always Load

**Old events should ALWAYS load without error.** Production systems may resume conversations that contain events serialized with older SDK versions. Breaking changes to event schemas will cause production failures.

**Important**: Deprecated field handlers are **permanent** and should never be removed. They ensure old conversations can always be loaded, regardless of when they were created.

### When Removing a Field from an Event Type

1. **Never use `extra="forbid"` without a deprecation handler** - This will reject old events that contain removed fields.

2. **Add a model validator to handle deprecated fields** using the `handle_deprecated_model_fields` utility:
   ```python
   from openhands.sdk.utils.deprecation import handle_deprecated_model_fields

   class MyModel(BaseModel):
       model_config = ConfigDict(extra="forbid")

       # Deprecated fields that are silently removed for backward compatibility
       # when loading old events. These are kept permanently.
       _DEPRECATED_FIELDS: ClassVar[tuple[str, ...]] = ("old_field_name",)

       @model_validator(mode="before")
       @classmethod
       def _handle_deprecated_fields(cls, data: Any) -> Any:
           """Remove deprecated fields for backward compatibility with old events."""
           return handle_deprecated_model_fields(data, cls._DEPRECATED_FIELDS)
   ```

3. **Write tests that verify both old and new event formats load correctly**:
   - Test that old format (with deprecated field) loads successfully
   - Test that new format (without deprecated field) works
   - Test that loading a sequence of mixed old/new events works

### Test Naming Convention for Event Backward Compatibility Tests

**The version in the test name should be the LAST version where a particular event structure exists.**

For example, if `enable_truncation` was removed in v1.11.1, the test should be named `test_v1_10_0_...` (the last version with that field).

This convention:
- Makes it clear which version's format is being tested
- Avoids duplicate tests for the same structure across multiple versions
- Documents when a field was last present in the schema

Example test names:
- `test_v1_10_0_text_content_with_enable_truncation` - Tests the last version with `enable_truncation`
- `test_v1_9_0_message_with_deprecated_fields` - Tests the last version with Message deprecated fields
- `test_text_content_current_format` - Tests the current format (no version needed)

### Example: See `TextContent` and `Message` in `openhands/sdk/llm/message.py`

These classes demonstrate the proper pattern for handling deprecated fields while maintaining backward compatibility with persisted events.

## Public API Removal Policy

Symbols exported via `openhands.sdk.__all__` are the SDK's public surface. Two CI policies govern changes:

1. **Deprecation before removal** – before removing a public API object, it must have been marked deprecated using the canonical helpers in `openhands.sdk.utils.deprecation`, and the deprecation must declare a removal target at least **5 minor releases** after `deprecated_in`.

   This applies to:
   - Removing a symbol from `openhands.sdk.__all__`.
   - Removing a public class member (method/property/attribute) from a class that is exported via `openhands.sdk.__all__`.

   Acceptable deprecation markers:
   - `@deprecated(deprecated_in=..., removed_in=...)` decorator for functions/classes/methods
   - `warn_deprecated(feature, deprecated_in=..., removed_in=...)` for runtime paths (e.g., attribute accessors). For members, use a qualified feature name like `"LLM.some_method"`.

   Note: Deprecating a class counts as deprecating its members for the purposes of member removal.

2. **MINOR version bump** – any breaking change (removal or structural) requires at least a MINOR version bump.

These are enforced by `check_sdk_api_breakage.py` (runs on release PRs). Deprecation deadlines are separately enforced by `check_deprecations.py` (runs on every PR).

## Documentation workflow

Documentation lives in **github.com/OpenHands/docs** under the `sdk/` folder. When adding features or modifying APIs, you MUST update documentation there.

### Workflow

1. Clone docs repo: `git clone https://github.com/OpenHands/docs.git /workspace/project/openhands-docs`
2. Create matching branch in both repos
3. Update documentation in `openhands-docs/sdk/` folder
4. **If you are creating a PR to `OpenHands/agent-sdk`**, you must also create a corresponding PR to `OpenHands/docs` with documentation updates in the `sdk/` folder
5. Cross-reference both PRs in their descriptions

Example:
```bash
cd /workspace/project/openhands-docs
git checkout -b <feature-name>
# Edit files in sdk/ folder
git add sdk/
git commit -m "Document <feature>

Co-authored-by: openhands <openhands@all-hands.dev>"
git push -u origin <feature-name>
```

## Running SDK examples

When implementing or modifying examples in `examples/`, always verify they work before committing:

```bash
# Run examples using the All-Hands LLM proxy
LLM_BASE_URL="https://llm-proxy.eval.all-hands.dev" LLM_API_KEY="$LLM_API_KEY" \
  uv run python examples/01_standalone_sdk/<example_name>.py
```

The `LLM_API_KEY` environment variable may be available in the OpenHands development environment and works with the All-Hands LLM proxy (`llm-proxy.eval.all-hands.dev` OR `llm-proxy.app.all-hands.dev`). Please consult the human user for the LLM key if it is not found.

For examples that use the critic model (e.g., `34_critic_example.py`), the critic is auto-configured when using the All-Hands LLM proxy - no additional setup needed.

## Commit & Pull Request Guidelines

- Follow the repository’s existing commit style (short, imperative subjects; use scope prefixes like `fix(sdk):` when helpful).
- Keep PRs focused; update docs and tests when changing public APIs or user-facing behavior.
