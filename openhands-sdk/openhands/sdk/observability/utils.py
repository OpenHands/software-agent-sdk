import os

from dotenv import dotenv_values

from openhands.sdk.event import ActionEvent


def get_env(key: str) -> str | None:
    """Get an environment variable from the environment or the dotenv file."""
    env_value = os.getenv(key)
    if env_value:
        return env_value
    try:
        return dotenv_values().get(key)
    except (AssertionError, OSError):
        return None


def extract_action_name(action_event: ActionEvent) -> str:
    try:
        if action_event.action is not None and hasattr(action_event.action, "kind"):
            return action_event.action.kind
        else:
            return action_event.tool_name
    except Exception:
        return "agent.execute_action"
