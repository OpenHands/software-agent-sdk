"""Switch a live conversation between the run's two real models through a
saved profile, and report which model ran each turn and what each cost.

Usage: live_switch.py   (in a run created with `control-sdk new --llm deepseek-flash`)
"""

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, LLMProfileStore


store = LLMProfileStore()  # $OH_PERSISTENCE_DIR/profiles: the run's own
store.save("pro", make_llm(model="deepseek-pro", usage_id="pro"), include_secrets=True)
conversation = Conversation(
    agent=Agent(llm=make_llm(usage_id="agent"), tools=[]),
    workspace=str(run_paths()["workspace"]),
    visualizer=None,
)
conversation.send_message("Reply with the single word: first.")
conversation.run()
first_model = conversation.agent.llm.model
conversation.switch_profile("pro")
conversation.send_message("Reply with the single word: second.")
conversation.run()
stats = conversation.conversation_stats.usage_to_metrics
emit(
    {
        "ok": True,
        "first_model": first_model,
        "second_model": conversation.agent.llm.model,
        "status": conversation.state.execution_status.value,
        "usage_ids": sorted(stats),
        "calls": {k: len(v.token_usages) for k, v in stats.items()},
    }
)
conversation.close()
