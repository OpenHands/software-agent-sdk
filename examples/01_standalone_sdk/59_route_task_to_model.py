"""Route each task to the best LLM with the built-in route_task_to_model tool.

The agent starts on a default profile. When it calls the ``route_task_to_model``
tool (a.k.a. ``ClassifyAndSwitchLLMTool``), a lightweight classifier LLM inspects
the recent conversation, picks the most suitable saved LLM profile for the task,
and switches the conversation to that profile before the agent continues.

This example shows the two meta-profile shapes the tool supports:

1. **Structured classes** — a fixed set of ``{description, model}`` rows; the
   classifier returns a class index.
2. **Direct prompt** — a ``prompt_template`` rendered with ``{{ instance_text }}``
   and ``{{ model_table }}``; the classifier returns the model name directly.

Both modes require the target models to exist as saved LLM profiles (the tool
switches to them by name) or to be supplied inline via ``meta_profile_llms``.

Usage:
    LLM_API_KEY=... LLM_BASE_URL=https://llm-proxy.app.all-hands.dev \
        uv run python examples/01_standalone_sdk/59_route_task_to_model.py
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, OpenHandsAgentSettings
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.meta_profile_store import MetaProfile, MetaProfileClass
from openhands.tools.preset.default import register_default_tools


DEFAULT_BASE_URL = "https://llm-proxy.app.all-hands.dev"

# Saved profile names. The route_task_to_model tool switches the conversation
# to one of these by name, so each must exist in the LLMProfileStore below.
DEFAULT_PROFILE = "example-router-default"
CHEAP_PROFILE = "example-router-cheap"
STRONG_PROFILE = "example-router-strong"
CLASSIFIER_PROFILE = "example-router-classifier"

CHEAP_MODEL = "openai/gpt-5.5"
STRONG_MODEL = "openai/prod/claude-sonnet-4-5-20250929"

api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)


# ── 1. Save the LLM profiles the router will choose between ──────────────
profile_store = LLMProfileStore()
for name, model, usage_id in [
    (DEFAULT_PROFILE, CHEAP_MODEL, "router-default"),
    (CHEAP_PROFILE, CHEAP_MODEL, "router-cheap"),
    (STRONG_PROFILE, STRONG_MODEL, "router-strong"),
    (CLASSIFIER_PROFILE, CHEAP_MODEL, "router-classifier"),
]:
    profile_store.save(
        name,
        LLM(
            model=model,
            api_key=SecretStr(api_key),
            base_url=base_url,
            usage_id=usage_id,
        ),
        include_secrets=True,
    )

try:
    # ── 2. Define a meta-profile: structured classes ──────────────────────
    # The classifier returns a 1-based class index; ``model`` is the saved
    # profile name to switch to. ``classifier_model`` is itself a saved
    # profile name used to run the classification call.
    structured_meta = MetaProfile(
        classifier_model=CLASSIFIER_PROFILE,
        classes=[
            MetaProfileClass(
                description="Simple lookups, small edits, formatting",
                model=CHEAP_PROFILE,
            ),
            MetaProfileClass(
                description="Multi-file reasoning, debugging, architecture",
                model=STRONG_PROFILE,
            ),
        ],
    )

    # ── 3. Build the agent via OpenHandsAgentSettings ─────────────────────
    # ``create_agent()`` defaults to the standard exec tools (terminal,
    # file_editor, task_tracker) by name; their implementations live in
    # ``openhands-tools`` and must be registered before the agent initializes.
    register_default_tools(enable_browser=False)

    # Enabling the tool + setting the active meta-profile name is all it takes
    # to wire route_task_to_model into the agent. The agent starts on the
    # default profile and switches only when it calls the tool.
    settings = OpenHandsAgentSettings(
        llm=profile_store.load(DEFAULT_PROFILE),
        enable_classify_and_switch_llm_tool=True,
        active_meta_profile="structured",
        meta_profile=structured_meta,
    )
    agent = settings.create_agent()

    conversation = Conversation(agent=agent, workspace=os.getcwd())
    print(f"Starting model: {conversation.agent.llm.model}")

    conversation.send_message(
        "Call the route_task_to_model tool now. After it returns, answer in one "
        "short sentence naming the model the tool switched to."
    )
    conversation.run()

    print(f"Active model after routing: {conversation.agent.llm.model}")

    for usage_id, metrics in conversation.state.stats.usage_to_metrics.items():
        print(f"  [{usage_id}] cost=${metrics.accumulated_cost:.6f}")
    combined = conversation.state.stats.get_combined_metrics()
    print(f"Total cost: ${combined.accumulated_cost:.6f}")
    print(f"EXAMPLE_COST: {combined.accumulated_cost}")

    # ── 4. (Info) Direct-prompt meta-profile shape ────────────────────────
    # Instead of fixed classes, you give the classifier a free-form prompt
    # template and a model table. The classifier returns the model name
    # directly as JSON ``{"model": "<name>", "reason": "..."}``. This is the
    # shape used by the Pareto prompt meta-profiles.
    direct_meta = MetaProfile(
        classifier_model=CLASSIFIER_PROFILE,
        prompt_template=(
            "Pick the best model for the task below.\n\n"
            "{{ model_table }}\n\n"
            "Task:\n{{ instance_text }}\n\n"
            'Return ONLY JSON: {"model": "<exact profile name>", "reason": "<short>"}'
        ),
        model_table=(
            f"- {CHEAP_PROFILE}: fast and cheap, good for simple tasks\n"
            f"- {STRONG_PROFILE}: slower and stronger, good for hard tasks"
        ),
    )
    print()
    print("Direct-prompt meta-profile (not executed here):")
    print(direct_meta.model_dump_json(indent=2))

finally:
    for name in [
        DEFAULT_PROFILE,
        CHEAP_PROFILE,
        STRONG_PROFILE,
        CLASSIFIER_PROFILE,
    ]:
        profile_store.delete(name)
