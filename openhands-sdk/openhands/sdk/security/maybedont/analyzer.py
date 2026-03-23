"""Maybe Don't Gateway security analyzer for OpenHands SDK.

This module provides a security analyzer that validates agent actions against
policy rules configured in the Maybe Don't Gateway. It calls the gateway's
action validation endpoint before actions are executed.

For more information, see: https://maybedont.ai/docs
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from pydantic import Field, PrivateAttr

from openhands.sdk.event import ActionEvent, LLMConvertibleEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.risk import SecurityRisk


logger = get_logger(__name__)

_RISK_MAP: dict[str, SecurityRisk] = {
    "high": SecurityRisk.HIGH,
    "medium": SecurityRisk.MEDIUM,
    "low": SecurityRisk.LOW,
    "unknown": SecurityRisk.UNKNOWN,
}


class MaybeDontAnalyzer(SecurityAnalyzerBase):
    """Security analyzer using the Maybe Don't Gateway for policy-based validation.

    This analyzer sends agent actions to a Maybe Don't Gateway instance for
    evaluation against configured CEL and AI-powered policy rules. The gateway
    returns a risk level that maps directly to SecurityRisk.

    The Maybe Don't Gateway supports two layers of protection:
    - **Security Analyzer** (this class): Pre-execution validation of ALL actions
      (shell commands, file ops, browser, tool calls)
    - **MCP Proxy** (separate config): Execution-time validation of MCP tool calls

    Environment Variables:
        MAYBE_DONT_GATEWAY_URL: Gateway base URL (default: http://localhost:8080)

    Example:
        >>> from openhands.sdk.security.maybedont import MaybeDontAnalyzer
        >>> analyzer = MaybeDontAnalyzer()
        >>> risk = analyzer.security_risk(action_event)
    """

    gateway_url: str | None = Field(
        default=None,
        description="Maybe Don't Gateway base URL (via MAYBE_DONT_GATEWAY_URL env var)",
    )
    timeout: float = Field(
        default=30.0,
        description="Request timeout in seconds",
    )
    client_id: str = Field(
        default="openhands",
        description="Client identifier for audit attribution",
    )

    _client: httpx.Client | None = PrivateAttr(default=None)
    _events: list[LLMConvertibleEvent] = PrivateAttr(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        """Initialize the analyzer after model creation."""
        # Resolve gateway URL: explicit param > env var > default
        if self.gateway_url is None:
            env_url = os.getenv("MAYBE_DONT_GATEWAY_URL")
            if env_url:
                self.gateway_url = env_url
                logger.debug(
                    "Gateway URL resolved from MAYBE_DONT_GATEWAY_URL "
                    "environment variable"
                )
            else:
                self.gateway_url = "http://localhost:8080"

        logger.info(
            f"MaybeDontAnalyzer initialized with gateway_url={self.gateway_url}, "
            f"timeout={self.timeout}s, client_id={self.client_id}"
        )

    def set_events(self, events: Any) -> None:
        """Store events for future use.

        The Maybe Don't Gateway does not currently use conversation history
        for action validation. Events are stored for interface compatibility
        and future extensibility.

        Args:
            events: Sequence of events (stored but not used in v1)
        """
        self._events = list(events)

    def _create_client(self) -> httpx.Client:
        """Create a new HTTP client instance."""
        return httpx.Client(
            timeout=self.timeout,
            headers={
                "Content-Type": "application/json",
                "X-Maybe-Dont-Client-ID": self.client_id,
            },
        )

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = self._create_client()
        elif self._client.is_closed:
            self._client = self._create_client()
        return self._client

    def _build_request(self, action: ActionEvent) -> dict[str, Any]:
        """Build the action validation request body from an ActionEvent.

        Maps ActionEvent fields to the gateway's ActionValidationRequest format.

        Args:
            action: The ActionEvent to convert

        Returns:
            Dictionary matching the gateway's expected request format
        """
        # Parse tool_call arguments from JSON string to dict
        parameters: dict[str, Any] = {}
        if action.tool_call and action.tool_call.arguments:
            try:
                parameters = json.loads(action.tool_call.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    f"Failed to parse tool_call arguments for {action.tool_name}, "
                    "sending empty parameters"
                )

        # Build context from agent reasoning
        context: dict[str, str] = {}
        if action.thought:
            thought_text = " ".join(t.text for t in action.thought if t.text)
            if thought_text:
                context["thought"] = thought_text
        if action.summary:
            context["summary"] = action.summary

        # v1: All actions are sent as "tool_call" — the gateway evaluates them
        # uniformly via EvaluateToolCall(). A future version may route by
        # action_type for type-specific evaluation.
        request: dict[str, Any] = {
            "action_type": "tool_call",
            "target": action.tool_name,
            "parameters": parameters,
            "actor": self.client_id,
        }
        if context:
            request["context"] = context

        return request

    def _map_response_to_risk(self, response: dict[str, Any]) -> SecurityRisk:
        """Map gateway response to SecurityRisk.

        Args:
            response: Parsed JSON response from the gateway

        Returns:
            SecurityRisk level based on the gateway's risk_level field
        """
        risk_level = response.get("risk_level", "unknown")
        return _RISK_MAP.get(risk_level, SecurityRisk.UNKNOWN)

    def _call_gateway(self, payload: dict[str, Any]) -> SecurityRisk:
        """Call the Maybe Don't Gateway action validation endpoint.

        Args:
            payload: Request body for the action validation endpoint

        Returns:
            SecurityRisk level based on gateway response
        """
        url = f"{self.gateway_url.rstrip('/')}/api/v1/action/validate"

        try:
            client = self._get_client()

            logger.debug(
                f"Sending action validation request to {url} "
                f"for target: {payload.get('target')}"
            )

            response = client.post(url, json=payload)

            if response.status_code == 200:
                try:
                    result = response.json()
                except json.JSONDecodeError:
                    logger.error(
                        f"Invalid JSON from Maybe Don't Gateway: {response.text}"
                    )
                    return SecurityRisk.UNKNOWN

                risk = self._map_response_to_risk(result)
                allowed = result.get("allowed", True)

                logger.info(
                    f"Maybe Don't risk assessment: {risk.name} "
                    f"(allowed={allowed}, target={payload.get('target')})"
                )
                return risk

            elif response.status_code == 400:
                logger.error(
                    f"Maybe Don't Gateway rejected request (400): {response.text}"
                )
                return SecurityRisk.UNKNOWN

            else:
                logger.error(
                    f"Maybe Don't Gateway error {response.status_code}: {response.text}"
                )
                return SecurityRisk.UNKNOWN

        except httpx.TimeoutException:
            logger.error("Maybe Don't Gateway request timed out")
            return SecurityRisk.UNKNOWN
        except Exception as e:
            logger.error(f"Maybe Don't Gateway request failed: {e}")
            return SecurityRisk.UNKNOWN

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        """Analyze action for security risks using the Maybe Don't Gateway.

        Converts the ActionEvent to the gateway's request format and calls the
        action validation endpoint. The gateway evaluates the action against
        configured CEL and AI-powered policy rules.

        Args:
            action: The ActionEvent to analyze

        Returns:
            SecurityRisk level based on gateway policy evaluation
        """
        logger.debug(
            f"Calling security_risk on MaybeDontAnalyzer for action: {action.tool_name}"
        )

        try:
            payload = self._build_request(action)
            return self._call_gateway(payload)
        except Exception as e:
            logger.error(f"Maybe Don't security analysis failed: {e}")
            return SecurityRisk.UNKNOWN

    def close(self) -> None:
        """Clean up resources."""
        if self._client is not None and not self._client.is_closed:
            self._client.close()
            self._client = None
