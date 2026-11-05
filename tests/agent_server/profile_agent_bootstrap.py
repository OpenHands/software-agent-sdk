"""Trusted startup fixture for the saved-profile selection tests."""

from uuid import UUID, uuid4

from pydantic import PrivateAttr

from openhands.sdk import Agent
from openhands.sdk.agent import register_agent


class ProfileSelectionAgent(Agent):
    """Stock behavior with process-local observation markers only."""

    _instance_token: UUID = PrivateAttr(default_factory=uuid4)
    _owner_attachment: object | None = PrivateAttr(default=None)

    @property
    def instance_token(self) -> UUID:
        return self._instance_token

    @property
    def owner_attachment_absent(self) -> bool:
        return self._owner_attachment is None


register_agent(ProfileSelectionAgent)
