"""Live check: ACPAgent applies `<model>/<effort>` to claude-agent-acp.

Logs every set_config_option call and the server's resulting model/effort
currentValue, then sends one short prompt.

Usage: uv run python .pr/live_effort_check.py opus[1m]/high
"""

import os
import sys

from acp import ClientSideConnection

from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation import Conversation
from openhands.sdk.settings.acp_providers import ACP_PROVIDERS


_original = ClientSideConnection.set_config_option


async def _logged_set_config_option(self, *args, **kwargs):
    print(f"set_config_option({kwargs.get('config_id')}={kwargs.get('value')!r})")
    try:
        response = await _original(self, *args, **kwargs)
    except Exception as e:
        print(f"  -> rejected: {e}")
        raise
    current = {
        o.id: o.current_value
        for o in response.config_options or []
        if o.id in ("model", "effort")
    }
    print(f"  -> server state: {current}")
    return response


ClientSideConnection.set_config_option = _logged_set_config_option

model = sys.argv[1]
agent = ACPAgent(
    acp_command=list(ACP_PROVIDERS["claude-code"].default_command),
    acp_model=model,
)
try:
    conversation = Conversation(agent=agent, workspace=os.getcwd())
    conversation.send_message("Reply with exactly: OK")
    conversation.run()
    print(f"acp_model={agent.acp_model!r} current_model_id={agent._current_model_id!r}")
finally:
    agent.close()
