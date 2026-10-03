import os
import shutil
import json
from openhands.sdk import LLM, Conversation, Agent, Tool
from openhands.sdk.agent.replay_agent import SnapshotReplayAgent
from openhands.tools.terminal import TerminalTool

from dotenv import load_dotenv

load_dotenv()

#import logging module, then set logging level to DEBUG to see detailed logs during replay
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Get path to this script's directory to ensure relative paths work correctly. convert it to string

script_dir = str(os.path.dirname(os.path.abspath(__file__)))



# --- SETUP ---
persistence_path = f"{script_dir}/hello_world_recorded"
drift_log = f"{script_dir}/replay_drift.jsonl"

# Cleanup from previous runs
for path in [persistence_path, drift_log]:
    if os.path.exists(path):
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

# 1. Setup LLM
llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
)

# --- PHASE 1: RECORDING ---
print("--- Phase 1: Recording Session ---")
shapshot_agent = Agent(
    llm=llm,
    tools=[Tool(name=TerminalTool.name)],
)

conversation = Conversation(
    agent=shapshot_agent,
    workspace=os.getcwd(),
    persistence_dir=persistence_path
)

conversation.send_message("Write 3 facts about the current project into FACTS.txt.")

conversation.run()
print(f"Recording complete. Files created in {persistence_path}/\n")


# --- PHASE 2: REPLAYING ---
print(f"--- Phase 2: Replaying Session from {persistence_path} ---")

# Initialize the replay agent ONLY AFTER the recording is finished.
# This ensures model_post_init can successfully discover the recorded events.
replay_agent = SnapshotReplayAgent(
    llm=llm,
    tools=[Tool(name=TerminalTool.name)],
    replay_mode=True,
    replay_persistence=persistence_path,
    drift_log_path=drift_log,
)

replay_conversation = Conversation(
    agent=replay_agent,
    workspace=os.getcwd(),
    persistence_dir=persistence_path
)
replay_conversation.run()

print("\n--- Replay Finished ---")

# --- VALIDATION ---
if os.path.exists(drift_log):
    print(f"\nDrift log results ({drift_log}):")
    with open(drift_log, 'r') as f:
        for line in f:
            data = json.loads(line)
            print(f"Action: {data['Action']['tool_name']} -> {data['Action']['action'].get('command', data['Action']['action'].get('message'))}")
            print(f"Drift: {data['Drift from expected observation']}")
else:
    print("\nError: Drift log was not created.")

print("\nValidation complete!")
