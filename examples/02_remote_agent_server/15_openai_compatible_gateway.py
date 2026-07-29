"""Use the agent-server through an OpenAI-compatible Chat Completions client.

This example starts a local agent-server, stores an LLM profile, lists it through
``GET /v1/models``, then calls ``POST /v1/chat/completions`` with the OpenAI
Python SDK. The returned ``X-OpenHands-ServerConversation-ID`` header is passed
back on a second call to continue the same OpenHands conversation.
"""

import os
from uuid import UUID

import httpx
from openai import OpenAI
from scripts.utils import ManagedAPIServer


# The gateway runs a full OpenHands agent, but OpenAI clients still need a
# normal model-like name. We create an LLM profile below and expose it as
# `openhands_<profile_name>` through `/v1/models`.

api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
assert api_key is not None, "Set LLM_API_KEY or OPENAI_API_KEY."

llm_model = os.getenv("LLM_MODEL", "gpt-5-nano")
llm_base_url = os.getenv("LLM_BASE_URL")
profile_name = "gateway_demo"
gateway_model = f"openhands_{profile_name}"

# Start a local agent-server for the demo. `use_session_api_key=True` turns on
# authentication; the same key works as both `X-Session-API-Key` for native
# agent-server routes and `Authorization: Bearer ...` for OpenAI SDK calls.

with ManagedAPIServer(
    port=8770,
    use_session_api_key=True,
    extra_env={
        "OH_ENABLE_VNC": "0",
        "OH_ENABLE_VSCODE": "0",
        "OH_PRELOAD_TOOLS": "0",
        "OH_SECRET_KEY": "example-secret-key-for-demo-only-32b",
        "OH_WEBHOOKS": "[]",
    },
    health_request_timeout=2.0,
) as server:
    session_api_key = (
        os.getenv("SESSION_API_KEY")
        or os.getenv("OH_SESSION_API_KEYS_0")
        or server.session_api_key
    )
    assert session_api_key is not None

    # Use the native REST API once to create the profile that backs the gateway
    # model. After that, normal OpenAI SDK calls are enough for chat traffic.
    api_client = httpx.Client(
        base_url=server.base_url,
        headers={"X-Session-API-Key": session_api_key},
        timeout=120.0,
    )
    openai_client = OpenAI(
        api_key=session_api_key,
        base_url=f"{server.base_url}/v1",
        timeout=120.0,
    )

    llm_config = {"model": llm_model, "api_key": api_key}
    if llm_base_url:
        llm_config["base_url"] = llm_base_url

    # `gateway_demo` becomes visible to OpenAI clients as `openhands_gateway_demo`.
    profile_response = api_client.post(
        f"/api/profiles/{profile_name}",
        json={"llm": llm_config, "include_secrets": True},
    )
    assert profile_response.status_code == 201, profile_response.text

    models = openai_client.models.list()
    model_ids = [model.id for model in models.data]
    assert gateway_model in model_ids
    print(f"Gateway models include: {gateway_model}")

    # Ask through the OpenAI SDK. `with_raw_response` lets us read the custom
    # response header that identifies the OpenHands conversation created behind
    # this otherwise OpenAI-shaped request.

    first_response = openai_client.chat.completions.with_raw_response.create(
        model=gateway_model,
        messages=[
            {
                "role": "system",
                "content": "Answer directly and do not use tools.",
            },
            {
                "role": "user",
                "content": (
                    "In one sentence, explain what an OpenAI-compatible "
                    "agent-server gateway does."
                ),
            },
        ],
    )
    first_completion = first_response.parse()
    conversation_id = first_response.headers.get("X-OpenHands-ServerConversation-ID")
    assert conversation_id is not None
    UUID(conversation_id)

    first_answer = first_completion.choices[0].message.content
    print(f"First answer: {first_answer}")
    print(f"OpenHands conversation ID: {conversation_id}")

    persisted_response = api_client.get(f"/api/conversations/{conversation_id}")
    assert persisted_response.status_code == 200, persisted_response.text

    # The gateway keeps conversations by default. Passing the header back lets
    # another OpenAI-compatible request continue the same server-side agent
    # conversation instead of starting over.

    second_completion = openai_client.chat.completions.create(
        model=gateway_model,
        messages=[
            {
                "role": "user",
                "content": "Now answer in five words or fewer: what did I ask about?",
            }
        ],
        extra_headers={"X-OpenHands-ServerConversation-ID": conversation_id},
    )
    second_answer = second_completion.choices[0].message.content
    print(f"Second answer using same conversation: {second_answer}")

    conversation_response = api_client.get(f"/api/conversations/{conversation_id}")
    assert conversation_response.status_code == 200, conversation_response.text
    stats = conversation_response.json().get("stats") or {}
    usage_to_metrics = stats.get("usage_to_metrics") or {}
    accumulated_cost = sum(
        metrics.get("accumulated_cost", 0.0) for metrics in usage_to_metrics.values()
    )

    # Clean up the demo resources. Real applications can keep the conversation
    # ID and inspect it later through the native agent-server API.
    api_client.delete(f"/api/conversations/{conversation_id}")
    api_client.delete(f"/api/profiles/{profile_name}")
    api_client.close()

    print(f"EXAMPLE_COST: {accumulated_cost}")
