"""Defense-in-Depth Security: composing local analyzers with ConfirmRisky.

This example demonstrates how to wire the defense-in-depth analyzer family
into a conversation. The analyzers classify agent actions at the action
boundary; the confirmation policy decides whether to prompt the user.

Analyzer selection does not automatically change confirmation policy --
you must configure both explicitly.
"""

from openhands.sdk.security import (
    ConfirmRisky,
    EnsembleSecurityAnalyzer,
    PatternSecurityAnalyzer,
    PolicyRailSecurityAnalyzer,
    SecurityRisk,
)


# Create the analyzer ensemble
security_analyzer = EnsembleSecurityAnalyzer(
    analyzers=[
        PolicyRailSecurityAnalyzer(),
        PatternSecurityAnalyzer(),
    ]
)

# Confirmation policy: prompt the user for MEDIUM and above
confirmation_policy = ConfirmRisky(threshold=SecurityRisk.MEDIUM)

# Wire into a conversation:
#
#   conversation = Conversation(agent=agent, workspace=".")
#   conversation.set_security_analyzer(security_analyzer)
#   conversation.set_confirmation_policy(confirmation_policy)
#
# Every agent action now passes through the analyzer.
# HIGH -> confirmation prompt. MEDIUM -> confirmed. LOW -> allowed.
# UNKNOWN -> confirmed by default (confirm_unknown=True).

print("Defense-in-depth security analyzer configured.")
print(f"Analyzer: {security_analyzer}")
print(f"Confirmation policy: {confirmation_policy}")
print("EXAMPLE_COST: 0")
