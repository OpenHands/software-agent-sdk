"""The parts of ChatGPT-subscription access a program can reach without an
interactive login: cached credentials, the LLM `subscription_login` returns,
the parameters it sends, serialization and env rehydration.

Usage: subscription.py

Seeds `$OH_PERSISTENCE_DIR/auth` with unexpired (fake) OAuth credentials, as a
previous login would leave them, then calls `LLM.subscription_login`. The
requests go to a local Responses endpoint instead of chatgpt.com.
"""

import os
import time

from S02.fake_provider import FakeProvider
from sdk_verify import emit

from openhands.sdk import LLM
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.auth.credentials import CredentialStore, OAuthCredentials


def attempt(fn):
    try:
        return fn(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:300]


CredentialStore().save(
    OAuthCredentials(
        vendor="openai",
        access_token="cached-access-token",
        refresh_token="cached-refresh-token",
        expires_at=int(time.time() * 1000) + 3_600_000,
    )
)
out: dict = {
    "ok": True,
    "auth_files": sorted(os.listdir(CredentialStore().credentials_dir)),
}

llm, err = attempt(lambda: LLM.subscription_login(vendor="openai", model="gpt-5.5"))
out["login_error"] = err
if llm is not None:
    out |= {
        "is_subscription": llm.is_subscription,
        "model": llm.model,
        "base_url": llm.base_url,
        "stream": llm.stream,
        "uses_responses_api": llm.uses_responses_api(),
    }
_, out["unsupported_model_error"] = attempt(
    lambda: LLM.subscription_login(vendor="openai", model="gpt-4o")
)
# A fresh login needs consent; without a terminal and without a prior consent
# marker it refuses instead of opening a browser.
_, out["forced_login_error"] = attempt(
    lambda: LLM.subscription_login(vendor="openai", model="gpt-5.5", force_login=True)
)

if llm is not None:
    messages = [
        Message(role="system", content=[TextContent(text="Be brief.")]),
        Message(role="user", content=[TextContent(text="hi")]),
    ]
    with FakeProvider(
        [{"status": 429, "message": "Rate limit reached"}, {"text": "codex reply"}]
    ) as server:
        local = llm.model_copy(
            update={
                "base_url": server.url,
                "num_retries": 2,
                "retry_min_wait": 0,
                "retry_max_wait": 0,
                "prompt_cache_retention": "24h",
            }
        )
        reply, err = attempt(
            lambda: local.responses(messages, on_token=lambda _e: None)
        )
        bodies = [q["body"] for q in server.requests]
        headers = server.requests[-1]["headers"] if server.requests else {}
        out["requests"] = {
            "error": err,
            "text": "".join(getattr(c, "text", "") for c in reply.message.content)
            if reply
            else None,
            "count": len(bodies),
            "keys": sorted(bodies[-1]) if bodies else [],
            "omitted_everywhere": sorted(
                k
                for k in (
                    "temperature",
                    "max_output_tokens",
                    "include",
                    "reasoning",
                    "prompt_cache_retention",
                )
                if all(k not in b for b in bodies)
            ),
            "stream": bodies[-1].get("stream") if bodies else None,
            "store": bodies[-1].get("store") if bodies else None,
            "authorization_uses_cached_token": headers.get("authorization")
            == "Bearer cached-access-token",
            "originator": headers.get("originator"),
        }

    dumped = llm.model_dump_json()
    restored = LLM.model_validate_json(dumped)
    out["serialized"] = {
        "is_subscription": restored.is_subscription,
        "auth_type": restored.auth_type,
        "token_in_dump": "cached-access-token" in dumped,
    }

os.environ.update(
    {
        "LLM_MODEL": "gpt-5.5",
        "LLM_AUTH_TYPE": "subscription",
        "LLM_SUBSCRIPTION_VENDOR": "openai",
    }
)
from_env, err = attempt(LLM.load_from_env)
out["from_env"] = {
    "error": err,
    "is_subscription": from_env.is_subscription if from_env else None,
    "model": from_env.model if from_env else None,
}
emit(out)
