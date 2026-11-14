"""Ask the person a question with options.

``ask_user`` shows the person one question with two to four options (and a
recommended one) as a card; they answer in one tap or in their own words. The
call ends the agent's turn, and the answer arrives as the person's next
message. See :mod:`openhands.sdk.event.ask_user` for how a question stays
pending and how it is answered.

Usage:
    from openhands.tools.ask_user import AskUserTool

    agent = Agent(llm=llm, tools=[Tool(name=AskUserTool.name)])
"""

from openhands.tools.ask_user.definition import (
    AskUserAction,
    AskUserObservation,
    AskUserOption,
    AskUserTool,
)


__all__ = [
    "AskUserAction",
    "AskUserObservation",
    "AskUserOption",
    "AskUserTool",
]
