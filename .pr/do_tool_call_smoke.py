"""Single tool-calling request to DigitalOcean using the SDK's routing kwargs.

Usage:
    DO_API_KEY=<DigitalOcean model access key or PAT> \
        uv run python .pr/do_tool_call_smoke.py <do-model-id> [<do-model-id> ...]
"""

import os
import sys

import litellm

from openhands.sdk.llm.utils.openhands_provider import litellm_call_kwargs


API_KEY = os.environ["DO_API_KEY"]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]

for model in sys.argv[1:]:
    kwargs = litellm_call_kwargs(f"digitalocean/{model}", None)
    try:
        resp = litellm.completion(
            **kwargs,
            api_key=API_KEY,
            messages=[
                {"role": "system", "content": "Use tools when helpful."},
                {"role": "user", "content": "What's the weather in Lisbon?"},
            ],
            tools=TOOLS,
            max_tokens=200,
        )
        calls = resp.choices[0].message.tool_calls or []
        names = [c.function.name for c in calls]
        print(f"{'PASS' if names else 'FAIL'}  {model}: tool_calls={names}")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL  {model}: {type(e).__name__}: {str(e)[:200]}")
