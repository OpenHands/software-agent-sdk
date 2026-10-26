"""Exercise actual local persistence without making an LLM call."""

import json
import sys
from contextlib import closing
from pathlib import Path

from openhands.sdk import Agent, LLM
from openhands.sdk.conversation.impl.local_conversation import LocalConversation

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
agent = Agent(llm=LLM(model="deepseek/deepseek-flash"), tools=[])
observed = {}
with closing(
    LocalConversation(
        agent=agent,
        workspace=root,
        persistence_dir=root,
        max_budget_per_run=0.05,
        delete_on_close=False,
        visualizer=None,
    )
) as conversation:
    conversation_id = conversation.id
    path = root / conversation_id.hex / "base_state.json"
    observed["created_cap"] = json.loads(path.read_text())["max_budget_per_run"]
    with conversation.state:
        conversation.state.max_budget_per_run = 0.06
    assert conversation.max_budget_per_run == 0.06
    observed["state_mutation_property_cap"] = conversation.max_budget_per_run
    conversation.max_budget_per_run = 0.07
    assert conversation.state.max_budget_per_run == 0.07
    observed["property_mutation_state_cap"] = conversation.state.max_budget_per_run
    observed["property_mutation_saved_cap"] = json.loads(path.read_text())[
        "max_budget_per_run"
    ]

with closing(
    LocalConversation(
        agent=None,
        workspace=root,
        persistence_dir=root,
        conversation_id=conversation_id,
        delete_on_close=False,
        visualizer=None,
    )
) as resumed:
    observed["resumed_without_argument"] = resumed.max_budget_per_run
    assert resumed.max_budget_per_run == 0.07

with closing(
    LocalConversation(
        agent=None,
        workspace=root,
        persistence_dir=root,
        conversation_id=conversation_id,
        max_budget_per_run=0.08,
        delete_on_close=False,
        visualizer=None,
    )
) as overridden:
    observed["explicit_numeric_override"] = overridden.max_budget_per_run
    assert overridden.max_budget_per_run == 0.08
    assert json.loads(path.read_text())["max_budget_per_run"] == 0.08
    overridden.max_budget_per_run = None
    observed["cleared_state_cap"] = overridden.state.max_budget_per_run
    assert json.loads(path.read_text()).get("max_budget_per_run") is None

with closing(
    LocalConversation(
        agent=None,
        workspace=root,
        persistence_dir=root,
        conversation_id=conversation_id,
        delete_on_close=False,
        visualizer=None,
    )
) as cleared:
    observed["resumed_cleared_cap"] = cleared.max_budget_per_run
    assert cleared.max_budget_per_run is None

observed["llm_calls"] = 0
print("LOCAL_STATE_BUDGET_OK " + json.dumps(observed))
