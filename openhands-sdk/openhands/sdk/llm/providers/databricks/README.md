# Databricks AI Gateway provider

Native provider for Databricks Foundation Model APIs, routed through the
**Databricks AI Gateway**. Uses a direct `httpx` transport against the
gateway instead of routing HTTP through `litellm.completion`.

Implements the Partner Well-Architected Framework (PWAF) contract for
Databricks OSS connectors: isolated auth strategies, `<partner>_<product>/<version>`
User-Agent on every request, typed errors, retry/backoff, and metadata-first
routing with a safe name-pattern fallback.

## TL;DR — construct an LLM and use it

```python
from pydantic import SecretStr
from openhands.sdk import create_llm
from openhands.sdk.llm.message import Message, TextContent

llm = create_llm(
    model="databricks/databricks-claude-sonnet-4-5",
    databricks_host="https://adb-xxx.cloud.databricks.com",
    api_key=SecretStr("dapi..."),
    usage_id="my-agent",
)

resp = llm.completion(messages=[
    Message(role="user", content=[TextContent(text="Hello!")]),
])
print(resp.message.content[0].text)
```

No `databricks/` prefix + no explicit provider keyword means you get the
base `LLM` (default LiteLLM transport). Adding the `databricks/` prefix is
the only signal `create_llm` needs to route to this provider.

## Supported native APIs

| Family                | AI Gateway path                                                   | When selected |
|---|---|---|
| `ProviderFamily.OPENAI`           | `POST /serving-endpoints/{endpoint}/invocations`                    | Default — every `llm/v1/chat` endpoint |
| `ProviderFamily.OPENAI_RESPONSES` | `POST /serving-endpoints/v1/responses`                              | GPT-5 series (`databricks-gpt-5*`) |
| `ProviderFamily.ANTHROPIC`        | `POST /serving-endpoints/anthropic/v1/messages`                     | Claude models (`*claude*`) |
| `ProviderFamily.GEMINI`           | `POST /serving-endpoints/gemini/v1beta/models/{endpoint}:generateContent` | Gemini models (`*gemini*`) |

Routing is metadata-first
(`GET /api/2.0/serving-endpoints/{name}` → `foundation_model.api_types` /
`external_model.provider`) with a name-pattern fallback (see `models.py`).
Results are cached in-process with a 5-minute TTL.

See `_local/skills/databricks-ai-gateway-fm-apis/SKILL.md` for the
authoritative routing table, example payloads per family, and a runnable
`probe.py` that self-verifies every native path.

## Authentication

Five PWAF-compliant strategies, resolved in this priority order by
`resolve_credentials()`:

1. **U2M** — OAuth browser PKCE, tokens passed in via `stored_u2m_tokens`.
2. **M2M** — `databricks_client_id` + `databricks_client_secret`
   (service principal / client_credentials grant).
3. **PAT** — `api_key=SecretStr("dapi...")`.
4. **PROFILE** — `databricks_profile="DEFAULT"` (reads `~/.databrickscfg`;
   requires the `databricks` extra: `pip install openhands-sdk[databricks]`).
5. **UNIFIED** — fallback to the `databricks-sdk` unified auth chain
   (env vars, Azure MSI/Entra ID, etc.).

Defer to the **Databricks Partner PWAF skills** *(URL TBD)* for end-to-end
auth details (token caching, refresh policies, CLI profile selection).

Use `llm.auth_method` to see which strategy resolved.

## Discovery (picker UIs)

Listing AI-Gateway-shaped chat endpoints:

```python
from openhands.sdk.llm.providers.databricks import (
    DatabricksCredentials, list_chat_endpoints,
)

creds = DatabricksCredentials(host="...", get_token=lambda: "dapi...", auth_method="pat")
for ep in list_chat_endpoints(creds):
    print(ep.qualified_name, ep.endpoint_type)
```

`list_chat_endpoints` includes both `FOUNDATION_MODEL_API` and
`EXTERNAL_MODEL` endpoints (customer-configured gpt-5 / gemini /
claude proxies). The lighter `list_foundation_models` returns flat
`databricks/<name>` strings and is what `list_models_from_env` uses
with a 5-minute TTL cache.

## PWAF surfaces on `DatabricksLLM`

| Attribute                  | What it tells you                                                         |
|---|---|
| `llm.auth_method`          | Resolved strategy: `pat` / `m2m` / `u2m` / `profile` / `unified` / `env` |
| `llm.predicted_family`     | Family by name pattern only (pure compute, no HTTP)                       |
| `llm.resolve_family()`     | Authoritative family (metadata probe, cached; falls back to predicted)    |
| `llm.max_input_tokens`     | Context window from the model-capability table                            |
| `llm.max_output_tokens`    | Output budget (generous on reasoning models — gpt-5, gemini 2.5, gpt-oss) |

## Module layout

| Module          | Role                                                                 |
|---|---|
| `__init__.py`   | Public API (`DatabricksLLM`, `ProviderFamily`, discovery, auth types) |
| `llm.py`        | `DatabricksLLM` — Pydantic subclass of `LLM`; transport override     |
| `client.py`     | `DatabricksFMAPIClient` — `httpx` transport, family dispatch, retry  |
| `native.py`     | Per-family `to_native` / `from_native` adapters (request/response shaping) |
| `models.py`     | `ProviderFamily`, `AIGatewayPaths`, routing functions, token containers |
| `auth.py`       | Five credential strategies, `resolve_credentials()`, token providers  |
| `discovery.py`  | `list_chat_endpoints` / `list_foundation_models` + TTL cache          |
| `utils.py`      | `USER_AGENT`, `DatabricksTimeouts`, retry/backoff, error mapping      |

## Relationship to LiteLLM

The connector owns its own HTTP path — all wire traffic to the Databricks AI
Gateway goes through `client.py` (`httpx`), not through `litellm.completion`.
It does, however, interoperate with LiteLLM at the type boundary to stay
compatible with the OpenHands base `LLM` class:

- `client.py` returns a `litellm.types.utils.ModelResponse` so callers of the
  base `LLM.completion` API get the expected return shape.
- `utils.py` and `auth.py` raise `litellm.exceptions.*` for HTTP error mapping
  so retry/backoff behaves consistently with other providers.

Removing this type-level coupling would require decoupling the OpenHands base
`LLM` class itself from LiteLLM and is tracked as a separate investigation —
it is deliberately out of scope for this provider.

### Parked: true LiteLLM decoupling (Scope B)

The work needed to drop the residual LiteLLM type dependency is intentionally
parked and recorded here so it isn't forgotten:

- **Goal.** Remove both `litellm.types.utils.ModelResponse` and
  `litellm.exceptions` from the Databricks provider's public surface so a
  future OpenHands deployment could run this connector without LiteLLM in the
  dependency graph at all.
- **Blocker.** The OpenHands base `LLM` class (in the shared SDK) still
  accepts and returns `ModelResponse`, and its retry/backoff wiring catches
  `litellm.exceptions.*`. Changing only the Databricks connector would break
  that contract and ripple into every other provider plus the callers.
- **Shape of the fix (for the future investigation).**
  1. Introduce a small SDK-level `LLMResponse` dataclass that `LLM.completion`
     returns regardless of provider, with a thin adapter that today wraps
     `ModelResponse` for LiteLLM-backed providers.
  2. Replace `litellm.exceptions.*` with a set of SDK-owned error classes
     (`LLMAuthError`, `LLMRateLimitError`, …) and translate at the LiteLLM
     boundary only.
  3. Switch this provider to the new types, delete the two LiteLLM imports
     from `client.py` / `utils.py` / `auth.py`, and remove the dependency
     marker from the Databricks provider's optional extras.
- **Test plan.** Re-run the full `tests/sdk/llm/providers/databricks/` suite
  plus the shared `LLM` contract tests without `litellm` installed in a
  separate venv to prove the connector is truly standalone.
- **Why not now.** Would touch every other provider and the base `LLM`
  class; out of scope for the native-Databricks provider milestone.

## Testing

Every routing / adapter / auth path has unit coverage under
`tests/sdk/llm/providers/databricks/`. End-to-end calls against
`e2e-demo-field-eng` have been run live across all three native families
(Llama → OpenAI Chat, Claude → Anthropic, Gemini → `generateContent`) and
all five auth strategies.
