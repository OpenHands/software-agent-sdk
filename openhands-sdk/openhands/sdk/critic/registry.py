"""
Registry for managing critics.

This module provides a factory pattern for creating critics by name,
making it easy to add new critics without modifying existing code.
"""

from typing import ClassVar

from openhands.sdk.critic.base import BaseCritic


class CriticRegistry:
    """
    Registry for managing available critics.

    This class provides a factory pattern for creating critics by name,
    making it easy to add new critics without modifying existing code.
    """

    _critics: ClassVar[dict[str, type[BaseCritic]]] = {}

    @classmethod
    def register(cls, name: str, critic_class: type[BaseCritic]) -> None:
        """
        Register a critic class with a given name.

        Args:
            name: The name to register the critic under
            critic_class: The critic class to register
        """
        cls._critics[name] = critic_class

    @classmethod
    def create_critic(cls, name: str) -> BaseCritic:
        """
        Create a critic instance by name.

        Args:
            name: The name of the critic to create

        Returns:
            An instance of the requested critic

        Raises:
            ValueError: If the critic name is not registered
        """
        if name not in cls._critics:
            available = list(cls._critics.keys())
            raise ValueError(f"Unknown critic: {name}. Available critics: {available}")

        critic_class = cls._critics[name]
        return critic_class()

    @classmethod
    def list_critics(cls) -> list[str]:
        """
        Get a list of all registered critic names.

        Returns:
            List of registered critic names
        """
        return list(cls._critics.keys())

    @classmethod
    def clear_registry(cls) -> None:
        """
        Clear all registered critics.

        This is primarily useful for testing.
        """
        cls._critics.clear()
