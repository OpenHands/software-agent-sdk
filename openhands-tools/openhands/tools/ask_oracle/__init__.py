"""Ask-Oracle tool package.

Provides a read-only ``ask_oracle`` tool: a one-shot, tool-less sub-agent that
consults a stronger/more-capable LLM for a second opinion. The Oracle model is a
saved LLM profile, resolved by convention under the name ``oracle`` (see
``ORACLE_PROFILE_NAME``).

Usage:
    from openhands.tools.ask_oracle import AskOracleTool

    agent = Agent(
        llm=llm,
        tools=[Tool(name=AskOracleTool.name)],
    )

The agent's active conversation LLM is never switched. The Oracle call sends only
the Oracle system prompt plus the agent's question and optional context, without
forwarding conversation history or tools.
"""

from openhands.tools.ask_oracle.definition import (
    ORACLE_PROFILE_NAME,
    AskOracleAction,
    AskOracleObservation,
    AskOracleTool,
)
from openhands.tools.ask_oracle.impl import AskOracleExecutor


__all__ = [
    "ORACLE_PROFILE_NAME",
    "AskOracleAction",
    "AskOracleObservation",
    "AskOracleExecutor",
    "AskOracleTool",
]
