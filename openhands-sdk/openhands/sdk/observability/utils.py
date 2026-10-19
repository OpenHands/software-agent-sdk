import os

from openhands.sdk.event import ActionEvent


def get_env(key: str) -> str | None:
    """Get an environment variable from the process environment.

    Loading a ``.env`` file is intentionally left to the host application (for
    example, the CLI calls ``load_dotenv()`` once at startup). Reading straight
    from ``os.environ`` avoids python-dotenv's ``find_dotenv()`` call-stack
    walk, which executes ``assert frame.f_back is not None`` and raises an
    ``AssertionError`` in execution contexts whose frames have no on-disk file
    (threads running exec'd/embedded code, some async paths). See issue #1325.
    """
    return os.getenv(key)


def extract_action_name(action_event: ActionEvent) -> str:
    try:
        if action_event.action is not None and hasattr(action_event.action, "kind"):
            return action_event.action.kind
        else:
            return action_event.tool_name
    except Exception:
        return "agent.execute_action"
