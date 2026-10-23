"""Optional prompt-injection signatures from Agent Threat Rules (ATR).

Nothing here is active by default. Opt in by adding the tuples to the
injection buckets of a ``PatternSecurityAnalyzer``, which scans them against
every extracted field of an action (arguments, thought, reasoning, summary)::

    from openhands.sdk.security.defense_in_depth.atr_patterns import (
        ATR_INJECTION_HIGH_PATTERNS,
        ATR_INJECTION_MEDIUM_PATTERNS,
    )
    from openhands.sdk.security.defense_in_depth.pattern import (
        DEFAULT_INJECTION_HIGH_PATTERNS,
        DEFAULT_INJECTION_MEDIUM_PATTERNS,
        PatternSecurityAnalyzer,
    )

    analyzer = PatternSecurityAnalyzer(
        injection_high_patterns=[
            *DEFAULT_INJECTION_HIGH_PATTERNS,
            *ATR_INJECTION_HIGH_PATTERNS,
        ],
        injection_medium_patterns=[
            *DEFAULT_INJECTION_MEDIUM_PATTERNS,
            *ATR_INJECTION_MEDIUM_PATTERNS,
        ],
    )

The tuples come from ATR's enforce lane (``maturity: stable``), restricted to
rules tagged as prompt injection (OWASP LLM01:2025 and OWASP Agentic
ASI01:2026). ATR severity ``critical`` maps to HIGH; ``high`` and ``medium``
map to MEDIUM. Detector IDs are ``atr.<ATR rule id>`` and descriptions are the
ATR rule titles.

Each pattern compiles under ``re``, stays fast on adversarial input, and
matches none of its rule's true negatives or the benign corpora (code and
docs); conditions that only match benchmark scaffolding are dropped, and a
rule is kept only if the patterns left still match all of its true positives
after this package's normalization. These are filters applied when the data
is generated, not a measurement. A replay on public coding and data-analysis
agent records changed no result, which is weak evidence; precision on agent
thoughts and tool arguments has not been measured.
``scripts/generate_atr_patterns.py``
regenerates the data and writes a report of every rule or pattern it left
out, and why.
"""

from openhands.sdk.security.defense_in_depth._atr_pattern_data import (
    ATR_INJECTION_HIGH_PATTERNS,
    ATR_INJECTION_MEDIUM_PATTERNS,
    ATR_RELEASE,
)


__all__ = [
    "ATR_INJECTION_HIGH_PATTERNS",
    "ATR_INJECTION_MEDIUM_PATTERNS",
    "ATR_RELEASE",
]
