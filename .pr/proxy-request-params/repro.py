"""Reproducer for #5499: proxy-advertised request-parameter capabilities.

Queries a LiteLLM proxy's ``/v1/model/info`` for one alias and shows whether the
SDK keeps ``litellm_params.allowed_openai_params`` and derives capabilities from
it. Output is redacted to the fields relevant to the issue; the credential is
never printed and the rest of the model catalog is never shown.

Usage:
    # Authenticated live proxy (reads LLM_API_KEY from the environment):
    uv run python .pr/proxy-request-params/repro.py \
        --base-url https://llm-proxy.app.all-hands.dev

    # Same flow against an in-process proxy that requires a Bearer key and
    # serves the redacted entry quoted in #5499:
    uv run python .pr/proxy-request-params/repro.py --fixture
    uv run python .pr/proxy-request-params/repro.py --fixture --no-key
"""

import argparse
import json
import os
import secrets
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.options.chat_options import select_chat_options
from openhands.sdk.llm.utils.model_features import get_features


ALIAS = "deepseek-v4.1-flash"

# Redacted entry from the issue, served by --fixture.
FIXTURE_ENTRY = {
    "model_name": ALIAS,
    "litellm_params": {
        "model": "openrouter/deepseek/deepseek-v4.1-flash",
        "allowed_openai_params": ["reasoning_effort"],
    },
    "model_info": {
        "supports_function_calling": True,
        "supports_reasoning": True,
        "supports_reasoning_effort": None,
        "max_input_tokens": 1000000,
        "max_output_tokens": 131072,
    },
}


def start_fixture_proxy(required_key: str) -> tuple[str, ThreadingHTTPServer]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Authorization") != f"Bearer {required_key}":
                status, body = 401, {"error": {"type": "auth_error", "code": "401"}}
            else:
                status, body = 200, {"data": [FIXTURE_ENTRY]}
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}", server


def redacted_proxy_entry(base_url: str, api_key: str | None) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = httpx.get(f"{base_url}/v1/model/info", headers=headers, timeout=30)
    out: dict = {"http_status": response.status_code}
    if response.status_code != 200:
        return out
    entry = next(
        (e for e in response.json().get("data", []) if e.get("model_name") == ALIAS),
        None,
    )
    if entry is None:
        out["entry"] = None
        return out
    out["entry"] = {
        "model_name": entry["model_name"],
        "litellm_params.allowed_openai_params": entry.get("litellm_params", {}).get(
            "allowed_openai_params"
        ),
        "model_info.supports_reasoning": entry.get("model_info", {}).get(
            "supports_reasoning"
        ),
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--no-key", action="store_true")
    args = parser.parse_args()

    server = None
    if args.fixture:
        key = secrets.token_urlsafe(16)
        base_url, server = start_fixture_proxy(key)
    else:
        key = os.environ.get("LLM_API_KEY")
        base_url = args.base_url
    if args.no_key:
        key = None

    model = f"litellm_proxy/{ALIAS}"
    print("credential supplied:", bool(key))
    print("proxy /v1/model/info:", json.dumps(redacted_proxy_entry(base_url, key)))

    llm = LLM(
        model=model,
        base_url=base_url,
        api_key=SecretStr(key) if key else None,
        reasoning_effort="low",
        usage_id="repro",
    )
    info = llm.model_info or {}
    print("SDK model_info found:", llm.model_info is not None)
    print("SDK model_info.allowed_openai_params:", info.get("allowed_openai_params"))

    features = llm._model_features()
    print(
        "SDK features:",
        json.dumps(
            {
                "supports_reasoning_effort": features.supports_reasoning_effort,
                "supports_prompt_cache_key": features.supports_prompt_cache_key,
                "supports_vision": features.supports_vision,
            }
        ),
    )
    # Isolate the request-parameter contract from the generic
    # ``supports_reasoning`` flag, which says nothing about ``reasoning_effort``.
    without_generic_flag = {k: v for k, v in info.items() if k != "supports_reasoning"}
    contract_only = get_features(model, without_generic_flag)
    print(
        "supports_reasoning_effort from advertised request params alone:",
        contract_only.supports_reasoning_effort,
    )
    # Every ModelFeatures field that the advertised params change; anything
    # other than the advertised capability would be an accidental enable.
    without_params = {
        k: v for k, v in without_generic_flag.items() if k != "allowed_openai_params"
    }
    baseline = asdict(get_features(model, without_params))
    changed = {
        k: [baseline[k], v]
        for k, v in asdict(contract_only).items()
        if baseline[k] != v
    }
    print("features changed by advertised params [without, with]:", changed)
    request = select_chat_options(llm, {}, has_tools=True)
    print("chat request reasoning_effort:", request.get("reasoning_effort"))

    if server is not None:
        server.shutdown()


if __name__ == "__main__":
    main()
