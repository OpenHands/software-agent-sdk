"""Provenance and sanity checks over LiteLLM's model database (#4880)."""

import logging

import pytest

from openhands.sdk.llm.utils.model_cost_map import (
    describe_metadata_anomalies,
    model_cost_map_provenance,
    warn_on_metadata_anomalies,
)


class TestProvenance:
    def test_reports_what_this_process_actually_loaded(self):
        provenance = model_cost_map_provenance()

        assert provenance.source in {"remote", "local", "unknown"}
        assert provenance.model_count > 0
        assert len(provenance.fingerprint) == 64

    def test_fingerprint_is_stable_within_a_process(self):
        """Two readings must agree, or it is useless for correlating hosts."""
        assert (
            model_cost_map_provenance().fingerprint
            == model_cost_map_provenance().fingerprint
        )


class TestAnomalies:
    def test_ordinary_metadata_is_not_an_anomaly(self):
        assert (
            describe_metadata_anomalies(
                {
                    "max_input_tokens": 200_000,
                    "max_output_tokens": 64_000,
                    "supports_vision": True,
                    "supports_reasoning": False,
                    "litellm_provider": "anthropic",
                }
            )
            == []
        )

    def test_absent_keys_are_not_anomalies(self):
        """Most models declare only some of these; silence is normal."""
        assert describe_metadata_anomalies({"litellm_provider": "openai"}) == []
        assert describe_metadata_anomalies({}) == []
        assert describe_metadata_anomalies(None) == []

    @pytest.mark.parametrize(
        "info,expected",
        [
            ({"max_input_tokens": 10**12}, "exceeds"),
            ({"max_output_tokens": -1}, "is negative"),
            ({"max_tokens": "200000"}, "is not a number"),
            # bool is an int in Python; a flag smuggled into a limit is not one.
            ({"max_input_tokens": True}, "is not a number"),
            ({"max_input_tokens": float("inf")}, "is not finite"),
            ({"supports_vision": "yes"}, "not a boolean"),
            ({"supports_prompt_cache": 1}, "not a boolean"),
            ({"litellm_provider": ""}, "not a provider name"),
            ({"litellm_provider": 42}, "not a provider name"),
        ],
    )
    def test_implausible_values_are_reported(self, info, expected):
        anomalies = describe_metadata_anomalies(info)

        assert len(anomalies) == 1, anomalies
        assert expected in anomalies[0]

    @pytest.mark.parametrize(
        "info",
        [
            # Moderation and embedding models declare no output budget; zero is
            # the value, not a missing one. e.g. omni-moderation-latest,
            # vercel_ai_gateway/openai/text-embedding-3-large.
            {"max_output_tokens": 0, "max_tokens": 0},
            {"max_input_tokens": 0},
            # Published as floats upstream, e.g. xai/grok-4-fast-reasoning.
            {"max_input_tokens": 2000000.0, "max_output_tokens": 2000000.0},
        ],
    )
    def test_real_upstream_conventions_are_not_anomalies(self, info):
        """Guards the false positives an earlier draft produced.

        Scanning all 3817 live entries, a stricter version of these bounds
        flagged 23 legitimate models — every embedding and moderation entry,
        plus the grok-4-fast family. A check that noisy would be ignored.
        """
        assert describe_metadata_anomalies(info) == []

    def test_a_context_window_that_would_disable_condensation_is_caught(self):
        """The failure that motivates the numeric bound: an inflated limit means
        we never truncate, and every request blows the provider's real one."""
        assert describe_metadata_anomalies({"max_input_tokens": 10**12})

    def test_reports_every_anomaly_not_just_the_first(self):
        anomalies = describe_metadata_anomalies(
            {"max_input_tokens": -5, "supports_vision": "yes", "litellm_provider": ""}
        )

        assert len(anomalies) == 3


class TestWarning:
    def test_warns_with_provenance_so_the_report_is_actionable(self, caplog):
        with caplog.at_level(logging.WARNING):
            warn_on_metadata_anomalies("acme/whatever", {"max_input_tokens": 10**12})

        assert "acme/whatever" in caplog.text
        assert "exceeds" in caplog.text
        # Without the source, a report cannot be traced back to a document.
        assert "sha256=" in caplog.text

    def test_silent_on_ordinary_metadata(self, caplog):
        with caplog.at_level(logging.WARNING):
            warn_on_metadata_anomalies("acme/fine", {"max_input_tokens": 200_000})

        assert caplog.text == ""
