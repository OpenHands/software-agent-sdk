### The Feature

Make the request parameters a proxy deployment accepts a declared part of the `/v1/model/info` (and `/model/info`) response.

Today a client can only learn this by reading `litellm_params.allowed_openai_params` from each entry:

```json
{
  "model_name": "my-alias",
  "litellm_params": {
    "model": "openrouter/vendor/some-model",
    "allowed_openai_params": ["reasoning_effort"]
  },
  "model_info": {
    "supports_reasoning": true,
    "supported_openai_params": ["..."]
  }
}
```

The field shows up because `_get_proxy_model_info()` echoes the deployment's `litellm_params` after `remove_sensitive_info_from_deployment()`. The setting itself is documented ([Drop params → Set `allowed_openai_params` on config.yaml](https://docs.litellm.ai/docs/completion/drop_params#specify-allowed-openai-params-in-a-request)). Its presence in the `/v1/model/info` response is not documented, though. The endpoint docstring only says it returns config.yaml descriptions "except api key and api base".

`model_info.supported_openai_params` does not fill the gap. It comes from `litellm.get_model_info()` for the underlying model and leaves out the deployment's `allowed_openai_params`. At request time, `get_optional_params` uses `supported_params + allowed_openai_params`. As a result, the advertised list can differ from what the proxy accepts. The advertised list is also missing when the underlying model is not in the cost map.

Either of these would give clients a stable contract:

1. Document `litellm_params.allowed_openai_params` as a stable field of the `/v1/model/info` response.
2. Or expose the effective per-deployment set in `model_info`, e.g. merge `allowed_openai_params` into `supported_openai_params` or add a separate field.

### Motivation, pitch

Clients that route through a LiteLLM proxy alias need to decide whether to send optional parameters like `reasoning_effort`, `prompt_cache_key`, or `prompt_cache_retention`. The proxy operator has already declared this per deployment. Without a declared response field, clients end up hard-coding model-name lists or relying on undocumented response shapes.

The OpenHands SDK now reads `litellm_params.allowed_openai_params` from `/v1/model/info` as a best-effort signal (OpenHands/software-agent-sdk#5499). We'd prefer to depend on a documented contract.

### Are you a ML Ops Team?

No

---
_This issue was created by an AI agent (OpenHands) on behalf of Graham Neubig._
