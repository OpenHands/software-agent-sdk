"""GraySwan Cygnal security analyzer for OpenHands SDK.

This module provides a security analyzer that uses GraySwan's Cygnal API
for AI safety monitoring. It analyzes agent actions and conversation history
to detect potential security risks.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import Field, PrivateAttr, SecretStr, model_validator

from openhands.sdk.event import ActionEvent, LLMConvertibleEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.grayswan.utils import convert_events_to_openai_messages
from openhands.sdk.security.risk import SecurityRisk


logger = get_logger(__name__)


class GraySwanAnalyzer(SecurityAnalyzerBase):
    """Security analyzer using GraySwan's Cygnal API for AI safety monitoring.

    This analyzer sends conversation history and pending actions to the GraySwan
    Cygnal API for security analysis. The API returns a violation score which is
    mapped to SecurityRisk levels.

    Environment Variables:
        GRAYSWAN_API_KEY: Required API key for GraySwan authentication
        GRAYSWAN_POLICY_ID: Optional policy ID for custom GraySwan policy

    Example:
        >>> from openhands.sdk.security.grayswan import GraySwanAnalyzer
        >>> analyzer = GraySwanAnalyzer()
        >>> risk = analyzer.security_risk(action_event)
    """

    history_limit: int = Field(
        default=20,
        description="Number of recent events to include as context",
    )
    max_message_chars: int = Field(
        default=30000,
        description="Max characters for conversation processing",
    )
    timeout: float = Field(
        default=30.0,
        description="Request timeout in seconds",
    )
    low_threshold: float = Field(
        default=0.3,
        description="Risk threshold for LOW classification (score <= threshold)",
    )
    medium_threshold: float = Field(
        default=0.7,
        description="Risk threshold for MEDIUM classification (score <= threshold)",
    )
    api_url: str = Field(
        default="https://api.grayswan.ai/cygnal/monitor",
        description="GraySwan Cygnal API endpoint",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="GraySwan API key (via GRAYSWAN_API_KEY env var)",
    )
    policy_id: str | None = Field(
        default=None,
        description="GraySwan policy ID (via GRAYSWAN_POLICY_ID env var)",
    )

    # Internal state - not serialized (using PrivateAttr for Pydantic)
    _client: httpx.Client | None = PrivateAttr(default=None)
    _events: list[LLMConvertibleEvent] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def validate_thresholds(self) -> GraySwanAnalyzer:
        """Validate that thresholds are properly ordered."""
        if self.low_threshold >= self.medium_threshold:
            raise ValueError(
                f"low_threshold ({self.low_threshold}) must be less than "
                f"medium_threshold ({self.medium_threshold})"
            )
        return self

    def model_post_init(self, __context: Any) -> None:
        """Initialize the analyzer after model creation."""
        # Resolve API key from environment if not provided
        if self.api_key is None:
            env_key = os.getenv("GRAYSWAN_API_KEY")
            if env_key:
                self.api_key = SecretStr(env_key)
                logger.debug(
                    "API key resolved from GRAYSWAN_API_KEY environment variable"
                )

        if not self.api_key:
            # Design choice: Graceful degradation instead of fail-fast.
            # This allows deployment without API key configured, returning UNKNOWN
            # risk for all analyses. This is intentional to support environments
            # where security analysis is optional or configured later.
            logger.warning(
                "GRAYSWAN_API_KEY not set. GraySwanAnalyzer will return UNKNOWN risk."
            )

        # Resolve policy ID from environment if not provided
        if self.policy_id is None:
            self.policy_id = os.getenv("GRAYSWAN_POLICY_ID")

        if not self.policy_id:
            # Use GraySwan default coding agent policy
            self.policy_id = "689ca4885af3538a39b2ba04"
            logger.info(f"Using default GraySwan policy ID: {self.policy_id}")
        else:
            logger.info(f"Using GraySwan policy ID from environment: {self.policy_id}")

        logger.info(
            f"GraySwanAnalyzer initialized with history_limit={self.history_limit}, "
            f"timeout={self.timeout}s"
        )

    def set_events(self, events: Sequence[LLMConvertibleEvent]) -> None:
        """Set the events for context when analyzing actions.

        Args:
            events: Sequence of events to use as context for security analysis
        """
        self._events = list(events)

    def _create_client(self) -> httpx.Client:
        """Create a new HTTP client instance."""
        api_key_value = self.api_key.get_secret_value() if self.api_key else ""
        return httpx.Client(
            timeout=self.timeout,
            headers={
                "Authorization": f"Bearer {api_key_value}",
                "Content-Type": "application/json",
            },
        )

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        # Split condition to avoid AttributeError when _client is None
        if self._client is None:
            self._client = self._create_client()
        elif self._client.is_closed:
            self._client = self._create_client()
        return self._client

    def _map_violation_to_risk(self, violation_score: float) -> SecurityRisk:
        """Map GraySwan violation score to SecurityRisk.

        Args:
            violation_score: Score from 0.0 to 1.0 indicating violation severity

        Returns:
            SecurityRisk level based on configured thresholds
        """
        if violation_score <= self.low_threshold:
            return SecurityRisk.LOW
        elif violation_score <= self.medium_threshold:
            return SecurityRisk.MEDIUM
        else:
            return SecurityRisk.HIGH

    def _call_grayswan_api(self, messages: list[dict[str, Any]]) -> SecurityRisk:
        """Call GraySwan API with formatted messages.

        Args:
            messages: List of messages in OpenAI format

        Returns:
            SecurityRisk level based on API response
        """
        if not self.api_key:
            logger.warning("No API key configured, returning UNKNOWN risk")
            return SecurityRisk.UNKNOWN

        try:
            client = self._get_client()

            payload = {"messages": messages, "policy_id": self.policy_id}

            logger.debug(
                f"Sending request to GraySwan API with {len(messages)} messages "
                f"and policy_id: {self.policy_id}"
            )

            response = client.post(self.api_url, json=payload)

            if response.status_code == 200:
                try:
                    result = response.json()
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON from GraySwan API: {response.text}")
                    return SecurityRisk.UNKNOWN

                violation_score = result.get("violation")

                # Validate response structure
                if violation_score is None:
                    logger.error("GraySwan API response missing 'violation' field")
                    return SecurityRisk.UNKNOWN

                risk_level = self._map_violation_to_risk(violation_score)

                # Indirect prompt injection is auto-escalated to HIGH
                if result.get("ipi"):
                    risk_level = SecurityRisk.HIGH
                    logger.warning(
                        "Indirect prompt injection detected, escalating to HIGH risk"
                    )

                logger.info(
                    f"GraySwan risk assessment: {risk_level.name} "
                    f"(violation_score: {violation_score:.2f})"
                )
                return risk_level
            else:
                logger.error(
                    f"GraySwan API error {response.status_code}: {response.text}"
                )
                return SecurityRisk.UNKNOWN

        except httpx.TimeoutException:
            logger.error("GraySwan API request timed out")
            return SecurityRisk.UNKNOWN
        except Exception as e:
            logger.error(f"GraySwan security analysis failed: {e}")
            return SecurityRisk.UNKNOWN

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        """Analyze action for security risks using GraySwan API.

        This method converts the conversation history and the pending action
        to OpenAI message format and sends them to the GraySwan Cygnal API
        for security analysis.

        Args:
            action: The ActionEvent to analyze

        Returns:
            SecurityRisk level based on GraySwan analysis
        """
        logger.debug(
            f"Calling security_risk on GraySwanAnalyzer for action: {action.tool_name}"
        )

        if not self.api_key:
            logger.warning("No API key configured for GraySwan analysis")
            return SecurityRisk.UNKNOWN

        try:
            # Limit to recent history
            recent_events = self._events
            if len(recent_events) > self.history_limit:
                recent_events = recent_events[-self.history_limit :]

            # Convert events to OpenAI message format
            events_to_process: list[LLMConvertibleEvent] = list(recent_events) + [
                action
            ]
            openai_messages = convert_events_to_openai_messages(events_to_process)

            if not openai_messages:
                logger.warning("No valid messages to analyze")
                return SecurityRisk.UNKNOWN

            logger.debug(
                f"Converted {len(events_to_process)} events into "
                f"{len(openai_messages)} OpenAI messages for GraySwan analysis"
            )
            return self._call_grayswan_api(openai_messages)

        except Exception as e:
            logger.error(f"GraySwan security analysis failed: {e}")
            return SecurityRisk.UNKNOWN

    def close(self) -> None:
        """Clean up resources."""
        if self._client is not None and not self._client.is_closed:
            self._client.close()
            self._client = None
