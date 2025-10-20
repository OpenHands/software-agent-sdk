import os

from dotenv import dotenv_values


def get_env(key: str) -> str | None:
    """Get an environment variable from the environment or the dotenv file."""
    return os.getenv(key) or dotenv_values().get(key)
